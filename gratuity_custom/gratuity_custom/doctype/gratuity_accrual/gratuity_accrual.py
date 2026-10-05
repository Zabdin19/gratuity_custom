# Copyright (c) 2026, zainulabdin and contributors
# For license information, please see license.txt

import frappe
from frappe import _, bold
from frappe.model.document import Document
from frappe.query_builder import Criterion
from frappe.utils import flt, get_link_to_form, getdate, nowdate

from gratuity_custom.accounts import (
	get_employees_with_provision,
	get_gratuity_settings,
	get_provision_balances,
	validate_gratuity_accounts,
	validate_no_provision_entries_after,
)
from gratuity_custom.calculation import calculate_gratuity_liability, validate_gratuity_rule
from gratuity_custom.journal_entries import (
	ACCRUE,
	RELEASE,
	build_provision_lines,
	submit_gratuity_journal_entry,
	submit_reversal_of,
)

# Inputs a recalculation takes over from the reversed accrual. Status and every Journal Entry
# reference belong to the source transaction only and are deliberately not listed.
RECALCULATION_INPUT_FIELDS = (
	"naming_series",
	"company",
	"gratuity_rule",
	"from_date",
	"to_date",
	"expense_account",
	"expense_reversal_account",
	"provision_account",
	"cost_center",
)


class GratuityAccrual(Document):
	def validate(self):
		self.set_missing_values()
		self.validate_dates()
		validate_gratuity_accounts(
			self.company,
			self.expense_account,
			self.expense_reversal_account,
			self.provision_account,
			mandatory=True,
		)
		validate_gratuity_rule(self.gratuity_rule)
		self.validate_cost_center()
		self.validate_recalculation_of()
		self.validate_employees()
		self.validate_conflicting_accruals()
		self.validate_posting_date_after_provision_entries()
		self.calculate()
		self.set_status()

	def before_submit(self):
		if not self.employees:
			frappe.throw(_("Add at least one employee"))
		# Serialise concurrent accruals for the same employees, then re-read balances under the lock.
		self.lock_employees()
		self.validate_conflicting_accruals()
		self.validate_posting_date_after_provision_entries()
		self.calculate()

		errors = [
			f"{row.employee}: {row.calculation_error}" for row in self.employees if row.calculation_error
		]
		if errors:
			frappe.throw("<br>".join(errors), title=_("Gratuity could not be calculated for some employees"))

	def on_submit(self):
		self.post_opening_reversal()
		self.post_provision()
		self.set_status(update=True)

	def before_cancel(self):
		if self.opening_reversal_journal_entry or self.provision_journal_entry:
			frappe.throw(
				_(
					"A posted Gratuity Accrual cannot be cancelled. Use Reverse Provision to reverse its provision entry."
				)
			)

	def on_cancel(self):
		self.set_status(update=True)

	# --- posting --------------------------------------------------------------------------------

	def post_opening_reversal(self):
		"""Method B, step 1: reverse each employee's existing provision balance.

		This reverses an aggregate GL balance (possibly built by several entries), so `reversal_of` is
		deliberately not set; the entry is traced through `gratuity_accrual` and its entry type.
		"""
		lines = build_provision_lines(
			self.provision_account,
			self.expense_reversal_account,
			[(r.employee, r.cost_center, r.previous_provision) for r in self.employees],
			RELEASE,
		)
		name = submit_gratuity_journal_entry(
			self.company,
			self.posting_date,
			"Opening Reversal",
			lines,
			_(
				"Gratuity Accrual {0}: reversal of existing provision balance before reposting for {1} to {2}"
			).format(self.name, self.from_date, self.to_date),
			gratuity_accrual=self.name,
		)
		self.db_set("opening_reversal_journal_entry", name)

	def post_provision(self):
		"""Method B, step 2: post the full liability as of the period end."""
		lines = build_provision_lines(
			self.provision_account,
			self.expense_account,
			[(r.employee, r.cost_center, r.liability) for r in self.employees],
			ACCRUE,
		)
		name = submit_gratuity_journal_entry(
			self.company,
			self.posting_date,
			"Provision",
			lines,
			_("Gratuity Accrual {0}: gratuity provision as of {1}").format(self.name, self.to_date),
			gratuity_accrual=self.name,
		)
		self.db_set("provision_journal_entry", name)

	# --- manual reversal & recalculation ----------------------------------------------------------

	@frappe.whitelist()
	def reverse_provision(self, posting_date=None):
		"""Reverse this accrual's Provision Journal Entry (Dr Provision / Cr Expense Reversal)."""
		if not frappe.has_permission(self.doctype, "submit", self):
			frappe.throw(
				_("Not permitted to reverse Gratuity Accrual {0}").format(self.name), frappe.PermissionError
			)

		posting_date = getdate(posting_date or nowdate())
		# Row lock: concurrent requests wait here and then see the updated status.
		current = frappe.db.get_value(
			self.doctype,
			self.name,
			["docstatus", "status", "reversal_journal_entry"],
			as_dict=True,
			for_update=True,
		)
		if current.docstatus != 1 or current.status != "Posted" or current.reversal_journal_entry:
			frappe.throw(
				_("Gratuity Accrual {0} has status {1} and cannot be reversed").format(
					bold(self.name), bold(current.status)
				),
				title=_("Already Reversed") if current.status == "Reversed" else None,
			)
		if not self.provision_journal_entry:
			frappe.throw(
				_("Gratuity Accrual {0} did not post any provision to reverse").format(bold(self.name))
			)
		if posting_date < getdate(self.posting_date):
			frappe.throw(
				_("Reversal date cannot be before the accrual posting date {0}").format(self.posting_date)
			)

		later = self.get_later_posted_accruals()
		if later:
			frappe.throw(
				_("Reverse the later posted Gratuity Accrual(s) first: {0}").format(
					", ".join(get_link_to_form("Gratuity Accrual", d) for d in later)
				)
			)

		name = submit_reversal_of(
			self.provision_journal_entry,
			posting_date,
			self.expense_account,
			self.expense_reversal_account,
			_("Reversal of gratuity provision posted by Gratuity Accrual {0}").format(self.name),
			self.name,
		)
		self.db_set("reversal_journal_entry", name)
		self.set_status(update=True)
		return name

	@frappe.whitelist()
	def create_recalculation(self):
		"""New draft accrual for the same period, linked to this reversed accrual."""
		if not frappe.has_permission(self.doctype, "create"):
			frappe.throw(_("Not permitted to create Gratuity Accrual"), frappe.PermissionError)
		if self.status != "Reversed":
			frappe.throw(_("Only a reversed Gratuity Accrual can be recalculated"))

		existing = frappe.db.get_value(
			self.doctype, {"recalculation_of": self.name, "docstatus": ("<", 2)}, "name"
		)
		if existing:
			frappe.throw(
				_("Recalculation {0} already exists for this accrual").format(
					get_link_to_form(self.doctype, existing)
				)
			)

		# Build a fresh document from the business inputs only. frappe.copy_doc is not used: by default
		# it also copies no_copy fields, which carried the source's status and Journal Entry references
		# (including its manual reversal) into the recalculation.
		new = frappe.new_doc(self.doctype)
		for fieldname in RECALCULATION_INPUT_FIELDS:
			new.set(fieldname, self.get(fieldname))
		for row in self.employees:
			# calculated columns are recomputed in validate
			new.append("employees", {"employee": row.employee, "cost_center": row.cost_center})
		new.recalculation_of = self.name
		new.posting_date = max(
			getdate(self.posting_date),
			getdate(frappe.db.get_value("Journal Entry", self.reversal_journal_entry, "posting_date")),
		)
		new.remarks = _("Recalculation of {0}").format(self.name)
		new.insert()
		return new.name

	def get_later_posted_accruals(self):
		accrual = frappe.qb.DocType("Gratuity Accrual")
		row = frappe.qb.DocType("Gratuity Accrual Employee")
		return (
			frappe.qb.from_(row)
			.join(accrual)
			.on(row.parent == accrual.name)
			.select(accrual.name)
			.distinct()
			.where(
				(accrual.docstatus == 1)
				& (accrual.status == "Posted")
				& (accrual.company == self.company)
				& (accrual.name != self.name)
				& (accrual.to_date > self.to_date)
				& (row.employee.isin([r.employee for r in self.employees]))
			)
		).run(pluck=True)

	def lock_employees(self):
		emp = frappe.qb.DocType("Employee")
		employees = sorted({row.employee for row in self.employees})
		frappe.qb.from_(emp).select(emp.name).where(emp.name.isin(employees)).for_update().run()

	def validate_posting_date_after_provision_entries(self):
		"""The previous provision is read as of the posting date, so nothing may be posted after it."""
		if self.provision_account:
			validate_no_provision_entries_after(
				self.company,
				self.provision_account,
				[row.employee for row in self.employees],
				self.posting_date,
			)

	# --- defaults & header validation -----------------------------------------------------------

	def set_missing_values(self):
		settings = get_gratuity_settings(self.company)
		self.expense_account = self.expense_account or settings.gratuity_expense_account
		self.expense_reversal_account = (
			self.expense_reversal_account or settings.gratuity_expense_reversal_account
		)
		self.provision_account = self.provision_account or settings.gratuity_provision_account
		self.gratuity_rule = self.gratuity_rule or settings.default_gratuity_rule
		self.cost_center = self.cost_center or settings.cost_center

	def validate_dates(self):
		if getdate(self.from_date) > getdate(self.to_date):
			frappe.throw(_("From Date cannot be after To Date"))
		if getdate(self.posting_date) < getdate(self.from_date):
			frappe.throw(_("Posting Date cannot be before From Date"))

	def validate_cost_center(self):
		if frappe.db.get_value("Cost Center", self.cost_center, "company") != self.company:
			frappe.throw(
				_("Cost Center {0} does not belong to company {1}").format(
					bold(self.cost_center), bold(self.company)
				)
			)

	def validate_recalculation_of(self):
		if not self.recalculation_of:
			return
		original = frappe.db.get_value(
			"Gratuity Accrual",
			self.recalculation_of,
			["status", "docstatus", "company", "from_date", "to_date"],
			as_dict=True,
		)
		if not original or original.docstatus != 1 or original.status != "Reversed":
			frappe.throw(
				_("Gratuity Accrual {0} must be submitted and reversed before it can be recalculated").format(
					bold(self.recalculation_of)
				)
			)
		if (original.company, getdate(original.from_date), getdate(original.to_date)) != (
			self.company,
			getdate(self.from_date),
			getdate(self.to_date),
		):
			frappe.throw(_("A recalculation must keep the company and period of the reversed accrual"))

	# --- employees ------------------------------------------------------------------------------

	def validate_employees(self):
		seen = set()
		for row in self.employees:
			if row.employee in seen:
				frappe.throw(
					_("Row {0}: Employee {1} is listed more than once").format(row.idx, bold(row.employee))
				)
			seen.add(row.employee)

			emp = frappe.db.get_value(
				"Employee",
				row.employee,
				["company", "status", "date_of_joining", "relieving_date"],
				as_dict=True,
			)
			if emp.company != self.company:
				frappe.throw(
					_("Row {0}: Employee {1} does not belong to company {2}").format(
						row.idx, bold(row.employee), bold(self.company)
					)
				)
			if emp.status not in ("Active", "Left") or (emp.status == "Left" and not emp.relieving_date):
				frappe.throw(
					_(
						"Row {0}: Employee {1} has status {2}; only Active employees or Left employees with a Relieving Date can be accrued"
					).format(row.idx, bold(row.employee), bold(emp.status))
				)
			if getdate(emp.date_of_joining) > getdate(self.to_date):
				frappe.throw(
					_("Row {0}: Employee {1} joins after {2}").format(
						row.idx, bold(row.employee), self.to_date
					)
				)

		settled = get_employees_with_submitted_gratuity(list(seen))
		if settled:
			frappe.throw(
				_(
					"Gratuity has already been settled (submitted Gratuity) for: {0}. Their provision is released at final settlement and cannot be accrued again."
				).format(", ".join(bold(e) for e in settled))
			)

	def validate_conflicting_accruals(self):
		"""One posted accrual per employee and period, and no accrual behind a later posted one.

		Reversed accruals do not count, which is what allows a recalculation of the same period.
		"""
		employees = [row.employee for row in self.employees]
		if not employees:
			return

		accrual = frappe.qb.DocType("Gratuity Accrual")
		row = frappe.qb.DocType("Gratuity Accrual Employee")
		conflicts = (
			frappe.qb.from_(row)
			.join(accrual)
			.on(row.parent == accrual.name)
			.select(row.employee, accrual.name, accrual.from_date, accrual.to_date)
			.where(
				(accrual.docstatus == 1)
				& (accrual.status == "Posted")
				& (accrual.company == self.company)
				& (accrual.name != (self.name or ""))
				& (accrual.to_date >= self.from_date)
				& (row.employee.isin(employees))
			)
			.orderby(row.employee)
		).run(as_dict=True)

		if conflicts:
			c = conflicts[0]
			overlaps = getdate(c.from_date) <= getdate(self.to_date)
			message = (
				_("Employee {0} already has posted Gratuity Accrual {1} for {2} to {3}.")
				if overlaps
				else _(
					"Employee {0} already has a later posted Gratuity Accrual {1} for {2} to {3}. Accruals must be posted in period order."
				)
			)
			frappe.throw(
				message.format(
					bold(c.employee), get_link_to_form("Gratuity Accrual", c.name), c.from_date, c.to_date
				)
				+ " "
				+ _("Reverse it first if this period must be recalculated."),
				title=_("Conflicting Gratuity Accrual"),
			)

	@frappe.whitelist()
	def get_employees(self):
		"""Fill the table with every employee the run should cover, then calculate."""
		self.check_permission("write")
		if self.docstatus != 0:
			frappe.throw(_("Employees can only be fetched for a draft accrual"))
		for field in ("company", "from_date", "to_date", "posting_date"):
			if not self.get(field):
				frappe.throw(_("Please set {0}").format(bold(_(self.meta.get_label(field)))))
		self.set_missing_values()

		self.set("employees", [])
		for employee in get_employees_for_accrual(
			self.company, self.from_date, self.to_date, self.provision_account, self.posting_date
		):
			self.append("employees", {"employee": employee})

		if not self.employees:
			frappe.msgprint(_("No employees found for this company and period"))
		self.calculate()
		return len(self.employees)

	# --- calculation ----------------------------------------------------------------------------

	def calculate(self):
		"""Recompute every row server-side; client-entered numbers are never trusted."""
		employees = [row.employee for row in self.employees]
		balances = (
			get_provision_balances(self.company, self.provision_account, employees, self.posting_date)
			if self.provision_account
			else {}
		)
		payroll_cost_centers = dict(
			frappe.get_all(
				"Employee",
				filters={"name": ("in", employees)},
				fields=["name", "payroll_cost_center"],
				as_list=True,
			)
		)

		for row in self.employees:
			result = calculate_gratuity_liability(row.employee, self.gratuity_rule, self.to_date)
			emp = frappe.db.get_value(
				"Employee", row.employee, ["employee_name", "date_of_joining", "relieving_date"], as_dict=True
			)
			row.update(
				{
					"employee_name": emp.employee_name,
					"date_of_joining": emp.date_of_joining,
					"relieving_date": emp.relieving_date,
					"as_of_date": result.as_of_date,
					"service_days": result.service_days,
					"non_working_days": result.non_working_days,
					"service_years": result.service_years,
					"is_eligible": int(result.is_eligible),
					"salary_slip": result.salary_slip,
					"basic_salary": result.basic_salary,
					"liability": flt(result.liability, row.precision("liability")),
					"previous_provision": flt(
						balances.get(row.employee), row.precision("previous_provision")
					),
					"calculation_error": result.error,
					"details": result.details,
				}
			)
			row.cost_center = row.cost_center or payroll_cost_centers.get(row.employee) or self.cost_center

		self.total_liability = flt(
			sum(flt(r.liability) for r in self.employees), self.precision("total_liability")
		)
		self.total_previous_provision = flt(
			sum(flt(r.previous_provision) for r in self.employees), self.precision("total_previous_provision")
		)

	def set_status(self, update=False):
		status = {0: "Draft", 2: "Cancelled"}.get(self.docstatus)
		if self.docstatus == 1:
			status = "Reversed" if self.reversal_journal_entry else "Posted"
		if update:
			self.db_set("status", status)
		else:
			self.status = status


def get_employees_with_submitted_gratuity(employees: list) -> list:
	if not employees:
		return []
	return frappe.get_all(
		"Gratuity",
		filters={"employee": ("in", employees), "docstatus": 1},
		pluck="employee",
		distinct=True,
	)


def get_employees_for_accrual(company, from_date, to_date, provision_account=None, balance_date=None) -> list:
	"""Active employees, employees relieved on/after the period start, and anyone still carrying a
	provision balance; excluding employees whose gratuity is already settled."""
	emp = frappe.qb.DocType("Employee")
	in_service = (
		frappe.qb.from_(emp)
		.select(emp.name)
		.where(
			(emp.company == company)
			& (emp.date_of_joining <= to_date)
			& Criterion.any(
				[
					emp.status == "Active",
					(emp.status == "Left") & (emp.relieving_date >= from_date),
				]
			)
		)
	).run(pluck=True)

	employees = set(in_service)
	if provision_account:
		with_balance = get_employees_with_provision(company, provision_account, balance_date or to_date)
		valid = frappe.get_all(
			"Employee",
			filters={"name": ("in", with_balance), "status": ("in", ("Active", "Left"))},
			pluck="name",
		)
		employees.update(valid)

	employees -= set(get_employees_with_submitted_gratuity(list(employees)))
	return sorted(employees)
