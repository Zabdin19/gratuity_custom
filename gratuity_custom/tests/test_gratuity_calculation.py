import inspect

import frappe
from frappe.utils import add_days, getdate
from hrms.payroll.doctype.gratuity.gratuity import Gratuity

from gratuity_custom.calculation import (
	HRMS_GRATUITY_API,
	calculate_gratuity_liability,
	validate_gratuity_rule,
)
from gratuity_custom.tests.utils import (
	GratuityTestCase,
	assign_salary,
	make_employee,
	make_employee_with_salary,
	make_gratuity_rule,
	make_salary_slip,
	setup_gratuity_company,
)

DOJ = "2019-01-01"


class TestGratuityCalculation(GratuityTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.setup = setup_gratuity_company()
		cls.company, cls.rule = cls.setup.company, cls.setup.rule
		cls.employee = make_employee_with_salary(
			cls.company, DOJ, 3000, ["2019-01-15"], first_name="_TG Base"
		)

	def calc(self, as_of, employee=None):
		return calculate_gratuity_liability(employee or self.employee, self.rule, as_of)

	def test_hrms_internal_api_available(self):
		"""Fails loudly if an HRMS upgrade renames/changes the Gratuity internals this app relies on."""
		for name, params in HRMS_GRATUITY_API.items():
			self.assertTrue(hasattr(Gratuity, name), f"HRMS Gratuity.{name} no longer exists")
			if params is not None:
				signature = list(inspect.signature(getattr(Gratuity, name)).parameters)[1:]
				self.assertEqual(signature, list(params), f"HRMS Gratuity.{name} signature changed")
		self.assertIsInstance(inspect.getattr_static(Gratuity, "gratuity_settings"), property)

	def test_below_365_days_not_eligible(self):
		result = self.calc(add_days(DOJ, 364))
		self.assertFalse(result.is_eligible)
		self.assertEqual(result.liability, 0)
		self.assertIsNone(result.error)

	def test_at_365_days_eligible(self):
		result = self.calc(add_days(DOJ, 365))
		self.assertEqual(result.service_days, 365)
		self.assertTrue(result.is_eligible)
		self.assertEqual(result.service_years, 1)
		self.assertEqual(result.liability, 2100)

	def test_first_five_years_at_21_days(self):
		self.assertEqual(self.calc(add_days(DOJ, 365 * 3)).liability, 6300)
		result = self.calc(add_days(DOJ, 365 * 5))
		self.assertEqual(result.basic_salary, 3000)
		self.assertEqual(result.liability, 10500)

	def test_after_five_years_at_30_days(self):
		self.assertEqual(self.calc(add_days(DOJ, 365 * 6)).liability, 13500)
		self.assertEqual(self.calc(add_days(DOJ, 365 * 7)).liability, 16500)

	def test_days_based_fractional_service(self):
		"""Standard 'Take Exact Completed Years' is days based: 2738 days = 7.501 years."""
		result = self.calc(add_days(DOJ, 2738))
		self.assertEqual(result.service_years, 7.501)
		self.assertEqual(result.liability, 18003)

	def test_not_yet_joined(self):
		result = self.calc(add_days(DOJ, -1))
		self.assertFalse(result.is_eligible)
		self.assertEqual(result.liability, 0)

	def test_uses_latest_salary_slip_on_or_before_as_of(self):
		employee = make_employee_with_salary(self.company, DOJ, 3000, ["2019-01-15"], first_name="_TG Raise")
		assign_salary(employee, self.company, 4000, "2022-01-01")
		make_salary_slip(employee, "2022-01-15")

		before = self.calc("2021-12-31", employee)
		self.assertEqual(before.basic_salary, 3000)

		after = self.calc("2022-01-31", employee)
		self.assertEqual(after.basic_salary, 4000)
		self.assertNotEqual(after.salary_slip, before.salary_slip)
		self.assertEqual(after.liability, round(4000 * 0.7 * after.service_years, 2))

	def test_lwp_reduces_service(self):
		employee = make_employee_with_salary(self.company, DOJ, 3000, ["2019-01-15"], first_name="_TG LWP")
		lwp = frappe.get_doc({"doctype": "Leave Type", "leave_type_name": "_Test Gratuity LWP", "is_lwp": 1})
		lwp.insert()
		for day in range(10):
			frappe.get_doc(
				{
					"doctype": "Attendance",
					"employee": employee,
					"company": self.company,
					"attendance_date": add_days("2019-06-03", day),
					"status": "On Leave",
					"leave_type": lwp.name,
				}
			).submit()

		at_365 = self.calc(add_days(DOJ, 365), employee)
		self.assertEqual(at_365.non_working_days, 10)
		self.assertFalse(at_365.is_eligible)

		at_375 = self.calc(add_days(DOJ, 375), employee)
		self.assertTrue(at_375.is_eligible)
		self.assertEqual(at_375.liability, 2100)

	def test_eligible_without_salary_slip_is_flagged(self):
		employee = make_employee(self.company, DOJ, first_name="_TG No Slip")
		self.assertIsNone(self.calc("2019-06-30", employee).error)  # not eligible: no slip needed

		result = self.calc("2021-06-30", employee)
		self.assertTrue(result.is_eligible)
		self.assertEqual(result.liability, 0)
		self.assertIn("No submitted Salary Slip", result.error)

	def test_as_of_capped_at_relieving_date(self):
		employee = make_employee_with_salary(self.company, DOJ, 3000, ["2019-01-15"], first_name="_TG Cap")
		frappe.db.set_value("Employee", employee, {"status": "Left", "relieving_date": "2026-08-17"})
		result = self.calc("2026-08-31", employee)
		self.assertEqual(result.as_of_date, getdate("2026-08-17"))

	def test_matches_standard_gratuity_document(self):
		"""Contract: as of the relieving date the service equals what the standard HRMS Gratuity computes."""
		employee = make_employee_with_salary(
			self.company, "2019-03-10", 3000, ["2026-07-15"], first_name="_TG Left"
		)
		frappe.db.set_value("Employee", employee, {"status": "Left", "relieving_date": "2026-08-17"})

		gratuity = frappe.get_doc(
			{
				"doctype": "Gratuity",
				"employee": employee,
				"company": self.company,
				"posting_date": "2026-08-17",
				"gratuity_rule": self.rule,
				"pay_via_salary_slip": 0,
				"expense_account": self.setup.accounts.expense,
				"payable_account": self.setup.accounts.payable,
			}
		).insert()

		result = self.calc("2026-08-17", employee)
		self.assertEqual(result.service_years, gratuity.current_work_experience)
		self.assertEqual(result.liability, gratuity.amount)
		self.assertGreater(result.liability, 0)

	def test_exact_mode_accepted(self):
		self.assertEqual(
			frappe.db.get_value("Gratuity Rule", self.rule, "work_experience_calculation_function"),
			"Take Exact Completed Years",
		)
		validate_gratuity_rule(self.rule)

	def test_round_mode_rejected(self):
		rounding = make_gratuity_rule("_Test Gratuity Rule Round", method="Round off Work Experience")
		with self.assertRaisesRegex(frappe.ValidationError, "requires .*Take Exact Completed Years"):
			validate_gratuity_rule(rounding)
		with self.assertRaisesRegex(frappe.ValidationError, "Round off Work Experience"):
			calculate_gratuity_liability(self.employee, rounding, add_days(DOJ, 3650))

	def test_manual_mode_rejected(self):
		manual = make_gratuity_rule("_Test Gratuity Rule Manual", method="Manual")
		with self.assertRaisesRegex(frappe.ValidationError, "Manual"):
			validate_gratuity_rule(manual)

	def test_below_365_days_cannot_become_eligible_through_rounding(self):
		as_of = add_days(DOJ, 364)
		rounding = make_gratuity_rule("_Test Gratuity Rule Round", method="Round off Work Experience")

		# What the rejected HRMS mode would do: round 364/365 = 0.997 up to 1 year -> eligible.
		engine = frappe.new_doc("Gratuity")
		engine.employee, engine.gratuity_rule = self.employee, rounding
		engine.get_total_working_days = lambda: 364
		self.assertEqual(engine.get_work_experience(), 1)

		# This app refuses that rule, and the approved Exact rule keeps the employee ineligible.
		self.assertRaises(
			frappe.ValidationError, calculate_gratuity_liability, self.employee, rounding, as_of
		)
		result = self.calc(as_of)
		self.assertFalse(result.is_eligible)
		self.assertEqual(result.liability, 0)
