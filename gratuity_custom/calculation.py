"""Gratuity liability as of a date, computed by the standard HRMS Gratuity engine.

This is the ONLY module in gratuity_custom that touches HRMS Gratuity internals. Standard HRMS can only
calculate gratuity for a relieved employee from their latest salary slip; a monthly accrual needs the
same calculation as of a period end. Everything that differs from standard is in this file:

* service is counted up to `as_of_date` (capped at the relieving date) instead of the relieving date,
  using the same day arithmetic and LWP/absence deduction as `Gratuity.get_total_working_days`;
* the applicable earnings come from the latest submitted Salary Slip whose start date is on or before
  `as_of_date`, read exactly like `Gratuity.get_total_component_amount` reads the latest slip;
* below the rule's minimum service the result is "not eligible" with zero liability, instead of an error.

Why a temporary, unsaved Gratuity object
-----------------------------------------
The slab arithmetic (`get_gratuity_amount`) and the experience/minimum-service rule
(`get_work_experience`) are instance methods of the standard Gratuity DocType, and they read their two
inputs through other instance methods: `get_total_working_days` (needs a relieving date) and
`get_total_component_amount` (latest salary slip, no date bound). To reuse the HRMS engine instead of
copying it, `_new_hrms_gratuity` creates a plain `frappe.new_doc("Gratuity")` that is never inserted, and
the two input methods are replaced *on that one instance only* (instance attributes) with the as-of
values above. The Gratuity class, its module and every other Gratuity document are untouched; there is
no subclass, no override_doctype_class and no monkey patch of HRMS.

HRMS methods relied on (all listed in `HRMS_GRATUITY_API`)
----------------------------------------------------------
* `Gratuity.gratuity_settings` (property): rule method, working days per year, minimum years
* `Gratuity.get_applicable_components()`: the rule's applicable earning components
* `Gratuity.get_non_working_days(relieving_date, status)`: LWP / absent days up to a date
* `Gratuity.get_work_experience()`: experience rounding + minimum-service check
* `Gratuity.get_gratuity_amount(experience)`: slab arithmetic
* `Gratuity.get_total_working_days()` / `get_total_component_amount()`: the two inputs supplied per instance

Upgrade protection
------------------
* `tests/test_gratuity_calculation.py::test_hrms_internal_api_available` fails if any method above is
  renamed or its signature changes.
* `tests/test_gratuity_calculation.py::test_matches_standard_gratuity_document` fails if, as of the
  relieving date, this service no longer produces exactly the experience and amount that a real standard
  Gratuity document calculates.
* The 21/30-day example tests (2100 / 10500 / 16500) pin the business results.

Only "Take Exact Completed Years" is supported: "Round off Work Experience" rounds before the minimum
service check (e.g. 364 days -> 1 year -> eligible), which contradicts the 365-day eligibility rule.
"""

from dataclasses import asdict, dataclass

import frappe
from frappe import _, bold
from frappe.utils import date_diff, flt, getdate

HRMS_GRATUITY_API = {
	"gratuity_settings": None,  # property: rule method, working days/year, minimum years
	"get_applicable_components": (),
	"get_non_working_days": ("relieving_date", "status"),
	"get_work_experience": (),
	"get_gratuity_amount": ("experience",),
	"get_total_working_days": (),  # supplied per instance
	"get_total_component_amount": (),  # supplied per instance
}

REQUIRED_EXPERIENCE_METHOD = "Take Exact Completed Years"


@dataclass
class GratuityLiability:
	employee: str
	as_of_date: object
	date_of_joining: object = None
	relieving_date: object = None
	service_days: int = 0  # calendar days from joining to as-of date (as-of day excluded, as in HRMS)
	non_working_days: float = 0  # LWP / absent days HRMS deducts from service
	service_years: float = 0
	is_eligible: bool = False
	salary_slip: str | None = None
	basic_salary: float = 0
	liability: float = 0
	error: str | None = None  # set when an eligible employee cannot be calculated (e.g. no salary slip)
	details: str = ""

	def as_dict(self):
		return frappe._dict(asdict(self))


def validate_gratuity_rule(gratuity_rule: str):
	rule = frappe.db.get_value(
		"Gratuity Rule", gratuity_rule, ["disable", "work_experience_calculation_function"], as_dict=True
	)
	if not rule:
		frappe.throw(_("Gratuity Rule {0} does not exist").format(bold(gratuity_rule)))
	if rule.disable:
		frappe.throw(_("Gratuity Rule {0} is disabled").format(bold(gratuity_rule)))
	if rule.work_experience_calculation_function != REQUIRED_EXPERIENCE_METHOD:
		frappe.throw(
			_(
				"Gratuity Rule {0} uses Work Experience Calculation method {1}. Gratuity provisioning requires {2}, so that service below the minimum (365 days) is never rounded up into eligibility."
			).format(
				bold(gratuity_rule),
				bold(rule.work_experience_calculation_function or _("Not Set")),
				bold(REQUIRED_EXPERIENCE_METHOD),
			),
			title=_("Unsupported Gratuity Rule"),
		)


def calculate_gratuity_liability(employee: str, gratuity_rule: str, as_of_date) -> GratuityLiability:
	# Enforced here as well as on the Gratuity Accrual, so no caller can calculate with a rounding rule.
	validate_gratuity_rule(gratuity_rule)
	emp = frappe.db.get_value("Employee", employee, ["date_of_joining", "relieving_date"], as_dict=True)
	as_of = getdate(as_of_date)
	if emp.relieving_date and getdate(emp.relieving_date) < as_of:
		as_of = getdate(emp.relieving_date)

	result = GratuityLiability(
		employee=employee,
		as_of_date=as_of,
		date_of_joining=emp.date_of_joining,
		relieving_date=emp.relieving_date,
	)
	if not emp.date_of_joining or getdate(emp.date_of_joining) > as_of:
		result.details = _("Not yet joined on {0}").format(as_of)
		return result

	engine = _new_hrms_gratuity(employee, gratuity_rule)
	settings = engine.gratuity_settings

	result.service_days = date_diff(as_of, emp.date_of_joining)
	result.non_working_days = flt(_get_non_working_days(engine, as_of))
	working_days = result.service_days - result.non_working_days
	result.service_years = flt(working_days / (settings.total_working_days_per_year or 1), 3)

	experience = _get_work_experience(engine, working_days)
	if experience is None:
		result.details = _("{0} working days / {1} = {2} years: below minimum of {3} year(s)").format(
			working_days,
			settings.total_working_days_per_year,
			result.service_years,
			settings.minimum_year_for_gratuity,
		)
		return result

	result.is_eligible = True
	result.service_years = experience

	slip = _get_salary_slip_as_of(employee, as_of)
	if not slip:
		result.error = _("No submitted Salary Slip starting on or before {0}").format(as_of)
		return result

	result.salary_slip = slip.name
	components = engine.get_applicable_components()
	applicable = [row for row in slip.earnings if row.salary_component in components]
	if not applicable:
		result.error = _("Salary Slip {0} has no applicable earning component for Gratuity Rule {1}").format(
			slip.name, gratuity_rule
		)
		return result

	result.basic_salary = sum(flt(row.default_amount) for row in applicable)
	result.liability = _get_gratuity_amount(engine, experience, result.basic_salary)
	result.details = _(
		"{0} days - {1} non-working = {2} days / {3} = {4} years; applicable earnings {5} ({6}); liability {7}"
	).format(
		result.service_days,
		result.non_working_days,
		working_days,
		settings.total_working_days_per_year,
		experience,
		result.basic_salary,
		slip.name,
		result.liability,
	)
	return result


# --- HRMS internals (keep every call below this line) ------------------------------------------------


def _new_hrms_gratuity(employee: str, gratuity_rule: str):
	"""Transient, never-saved standard Gratuity document used purely as the calculation engine."""
	engine = frappe.new_doc("Gratuity")
	engine.employee = employee
	engine.gratuity_rule = gratuity_rule
	return engine


def _get_non_working_days(engine, as_of) -> float:
	# Same selection as Gratuity.get_total_working_days, bounded by the as-of date.
	payroll_based_on = frappe.db.get_single_value("Payroll Settings", "payroll_based_on") or "Leave"
	if payroll_based_on == "Leave":
		return engine.get_non_working_days(as_of, "On Leave")
	if payroll_based_on == "Attendance":
		return engine.get_non_working_days(as_of, "Absent")
	return 0


def _get_work_experience(engine, working_days: float) -> float | None:
	"""HRMS experience (rounding per rule). None when below the rule's minimum service."""
	# Instance attribute on the transient engine only: replaces the relieving-date based input.
	engine.get_total_working_days = lambda: working_days
	try:
		return engine.get_work_experience()
	except frappe.ValidationError:
		# get_work_experience only raises for the minimum-service rule once working days are supplied.
		frappe.clear_last_message()
		return None


def _get_gratuity_amount(engine, experience: float, applicable_earnings: float) -> float:
	# Instance attribute on the transient engine only: replaces the "latest slip, any date" input.
	engine.get_total_component_amount = lambda: applicable_earnings
	return flt(engine.get_gratuity_amount(experience))


def _get_salary_slip_as_of(employee: str, as_of):
	# Mirrors hrms...gratuity.get_last_salary_slip (submitted, latest start_date) with an as-of bound.
	name = frappe.db.get_value(
		"Salary Slip",
		{"employee": employee, "docstatus": 1, "start_date": ("<=", as_of)},
		"name",
		order_by="start_date desc",
	)
	return frappe.get_doc("Salary Slip", name) if name else None
