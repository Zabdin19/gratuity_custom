# Copyright (c) 2026, zainulabdin and contributors
# For license information, please see license.txt

import frappe

from gratuity_custom.tests.utils import (
	GratuityTestCase,
	make_accrual,
	make_company,
	make_employee,
	make_employee_with_salary,
	make_gratuity_rule,
	setup_gratuity_company,
)

# Fixtures are built explicitly in setUpClass and rolled back; never let the runner auto-create
# (and commit) ERPNext/HRMS test records for linked doctypes.
test_ignore = [
	"Account",
	"Company",
	"Cost Center",
	"Currency",
	"Employee",
	"Gratuity Accrual",
	"Gratuity Rule",
	"Journal Entry",
	"Salary Slip",
]


class TestGratuityAccrual(GratuityTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.setup = setup_gratuity_company()
		cls.company = cls.setup.company
		# 1825 service days (5.0 years) on 2024-01-31
		cls.senior = make_employee_with_salary(
			cls.company, "2019-02-01", 3000, ["2024-01-15"], first_name="_TG Senior"
		)
		# below 365 days on 2024-01-31
		cls.junior = make_employee_with_salary(
			cls.company, "2023-06-01", 2000, ["2024-01-15"], first_name="_TG Junior"
		)

	def test_get_employees_selection(self):
		left_in_period = make_employee(self.company, "2020-01-01", first_name="_TG Left In Period")
		frappe.db.set_value("Employee", left_in_period, {"status": "Left", "relieving_date": "2024-01-20"})
		left_before = make_employee(self.company, "2020-01-01", first_name="_TG Left Before")
		frappe.db.set_value("Employee", left_before, {"status": "Left", "relieving_date": "2023-12-15"})
		not_joined = make_employee(self.company, "2024-02-05", first_name="_TG Future")
		inactive = make_employee(self.company, "2020-01-01", first_name="_TG Inactive", status="Inactive")

		accrual = make_accrual(self.company, "2024-01-01")
		employees = {row.employee for row in accrual.employees}

		self.assertTrue({self.senior, self.junior, left_in_period} <= employees)
		self.assertFalse({left_before, not_joined, inactive} & employees)

	def test_rows_are_calculated(self):
		accrual = make_accrual(self.company, "2024-01-01", employees=[self.senior, self.junior])
		senior = next(r for r in accrual.employees if r.employee == self.senior)
		junior = next(r for r in accrual.employees if r.employee == self.junior)

		self.assertTrue(senior.is_eligible)
		self.assertEqual(senior.basic_salary, 3000)
		self.assertEqual(senior.service_days, 1825)
		self.assertEqual(senior.liability, 10500)
		self.assertTrue(senior.salary_slip)
		self.assertEqual(senior.previous_provision, 0)

		self.assertFalse(junior.is_eligible)
		self.assertEqual(junior.liability, 0)
		self.assertEqual(accrual.total_liability, 10500)
		self.assertEqual(accrual.status, "Draft")

	def test_defaults_from_company(self):
		accrual = make_accrual(self.company, "2024-01-01", employees=[self.senior])
		self.assertEqual(accrual.expense_account, self.setup.accounts.expense)
		self.assertEqual(accrual.expense_reversal_account, self.setup.accounts.reversal)
		self.assertEqual(accrual.provision_account, self.setup.accounts.provision)
		self.assertEqual(accrual.gratuity_rule, self.setup.rule)
		self.assertTrue(accrual.cost_center)

	def test_client_values_are_recalculated(self):
		accrual = make_accrual(self.company, "2024-01-01", employees=[self.senior])
		accrual.employees[0].liability = 999999
		accrual.employees[0].previous_provision = 5
		accrual.save()
		self.assertEqual(accrual.employees[0].liability, 10500)
		self.assertEqual(accrual.employees[0].previous_provision, 0)

	def test_payable_provision_account_rejected(self):
		with self.assertRaisesRegex(frappe.ValidationError, "blank Account Type"):
			make_accrual(
				self.company,
				"2024-01-01",
				employees=[self.senior],
				provision_account=self.setup.accounts.payable,
			)

	def test_duplicate_employee_rejected(self):
		with self.assertRaisesRegex(frappe.ValidationError, "more than once"):
			make_accrual(self.company, "2024-01-01", employees=[self.senior, self.senior])

	def test_inactive_employee_rejected(self):
		inactive = make_employee(self.company, "2020-01-01", first_name="_TG Inactive 2", status="Inactive")
		with self.assertRaisesRegex(frappe.ValidationError, "status"):
			make_accrual(self.company, "2024-01-01", employees=[inactive])

	def test_employee_of_other_company_rejected(self):
		other = make_company("_Test Gratuity Company 2", "_TGC2").name
		outsider = make_employee(other, "2020-01-01", first_name="_TG Outsider")
		with self.assertRaisesRegex(frappe.ValidationError, "does not belong"):
			make_accrual(self.company, "2024-01-01", employees=[outsider])

	def test_invalid_dates_rejected(self):
		with self.assertRaisesRegex(frappe.ValidationError, "From Date cannot be after"):
			make_accrual(self.company, "2024-02-01", to_date="2024-01-31", employees=[self.senior])

	def test_cost_center_of_other_company_rejected(self):
		other = make_company("_Test Gratuity Company 2", "_TGC2").name
		other_cc = frappe.db.get_value("Company", other, "cost_center")
		with self.assertRaisesRegex(frappe.ValidationError, "Cost Center"):
			make_accrual(self.company, "2024-01-01", employees=[self.senior], cost_center=other_cc)

	def test_rounding_rule_rejected_on_accrual(self):
		rounding = make_gratuity_rule("_Test Gratuity Rule Round", method="Round off Work Experience")
		with self.assertRaisesRegex(frappe.ValidationError, "Take Exact Completed Years"):
			make_accrual(self.company, "2024-01-01", employees=[self.junior], gratuity_rule=rounding)

	def test_missing_accounts_rejected(self):
		bare = make_company("_Test Gratuity Company 3", "_TGC3").name
		employee = make_employee(bare, "2020-01-01", first_name="_TG Bare")
		with self.assertRaisesRegex(frappe.ValidationError, "Gratuity Expense Account"):
			make_accrual(bare, "2024-01-01", employees=[employee], gratuity_rule=self.setup.rule)


def je_lines(journal_entry):
	return frappe.get_all(
		"Journal Entry Account",
		filters={"parent": journal_entry},
		fields=["account", "party_type", "party", "debit", "credit", "cost_center"],
		order_by="idx",
	)


def account_total(lines, account, field):
	return sum(line[field] for line in lines if line.account == account)


class TestGratuityAccrualPosting(GratuityTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.setup = setup_gratuity_company()
		cls.company, cls.accounts = cls.setup.company, cls.setup.accounts
		cls.senior = make_employee_with_salary(
			cls.company, "2019-02-01", 3000, ["2024-01-15"], first_name="_TG Senior"
		)
		cls.junior = make_employee_with_salary(
			cls.company, "2023-06-01", 2000, ["2024-01-15"], first_name="_TG Junior"
		)

	def balance(self, employee, as_of="2099-12-31"):
		from gratuity_custom.accounts import get_provision_balances

		return get_provision_balances(self.company, self.accounts.provision, [employee], as_of).get(
			employee, 0
		)

	def post(self, from_date, employees=None, **kwargs):
		return make_accrual(
			self.company, from_date, employees=employees or [self.senior, self.junior], submit=True, **kwargs
		)

	def expected_liability(self, as_of):
		from gratuity_custom.calculation import calculate_gratuity_liability

		return calculate_gratuity_liability(self.senior, self.setup.rule, as_of).liability

	def test_provision_posting_dr_expense_cr_provision(self):
		jan = self.post("2024-01-01")
		self.assertEqual(jan.status, "Posted")
		self.assertFalse(jan.opening_reversal_journal_entry)  # no existing balance to reverse

		je = frappe.get_doc("Journal Entry", jan.provision_journal_entry)
		self.assertEqual(je.docstatus, 1)
		self.assertEqual((je.gratuity_entry_type, je.gratuity_accrual), ("Provision", jan.name))
		self.assertEqual(je.total_debit, je.total_credit)

		lines = je_lines(je.name)
		self.assertEqual(account_total(lines, self.accounts.expense, "debit"), 10500)
		provision = [line for line in lines if line.account == self.accounts.provision]
		self.assertEqual(len(provision), 1)  # ineligible junior has no line
		self.assertEqual(
			(provision[0].party_type, provision[0].party, provision[0].credit),
			("Employee", self.senior, 10500),
		)
		self.assertEqual(self.balance(self.senior), 10500)
		self.assertEqual(self.balance(self.junior), 0)

	def test_monthly_reverse_and_repost(self):
		jan = self.post("2024-01-01")
		feb = self.post("2024-02-01")
		feb_liability = self.expected_liability("2024-02-29")
		self.assertGreater(feb_liability, 10500)

		row = next(r for r in feb.employees if r.employee == self.senior)
		self.assertEqual((row.previous_provision, row.liability), (10500, feb_liability))

		opening = frappe.get_doc("Journal Entry", feb.opening_reversal_journal_entry)
		self.assertEqual(opening.gratuity_entry_type, "Opening Reversal")
		self.assertFalse(opening.reversal_of)  # aggregate balance, not one specific entry
		lines = je_lines(opening.name)
		self.assertEqual(account_total(lines, self.accounts.provision, "debit"), 10500)
		self.assertEqual(account_total(lines, self.accounts.reversal, "credit"), 10500)

		lines = je_lines(feb.provision_journal_entry)
		self.assertEqual(account_total(lines, self.accounts.expense, "debit"), feb_liability)
		self.assertEqual(account_total(lines, self.accounts.provision, "credit"), feb_liability)

		self.assertEqual(self.balance(self.senior), feb_liability)
		self.assertEqual(frappe.db.get_value("Gratuity Accrual", jan.name, "status"), "Posted")

	def test_duplicate_period_blocked(self):
		self.post("2024-01-01")
		with self.assertRaisesRegex(frappe.ValidationError, "already has posted"):
			make_accrual(self.company, "2024-01-01", employees=[self.senior])

	def test_back_dated_accrual_blocked(self):
		self.post("2024-02-01")
		with self.assertRaisesRegex(frappe.ValidationError, "later posted"):
			make_accrual(self.company, "2024-01-01", employees=[self.senior])

	def test_manual_reversal(self):
		jan = self.post("2024-01-01")
		original = frappe.get_doc("Journal Entry", jan.provision_journal_entry)
		reversal_name = jan.reverse_provision("2024-02-05")

		jan.reload()
		self.assertEqual((jan.status, jan.reversal_journal_entry), ("Reversed", reversal_name))

		reversal = frappe.get_doc("Journal Entry", reversal_name)
		self.assertEqual(reversal.reversal_of, original.name)
		self.assertEqual(
			(reversal.gratuity_entry_type, reversal.gratuity_accrual), ("Provision Reversal", jan.name)
		)
		lines = je_lines(reversal_name)
		self.assertEqual(account_total(lines, self.accounts.reversal, "credit"), 10500)
		self.assertEqual(account_total(lines, self.accounts.expense, "credit"), 0)
		provision = [line for line in lines if line.account == self.accounts.provision]
		self.assertEqual((provision[0].party, provision[0].debit), (self.senior, 10500))

		self.assertEqual(self.balance(self.senior), 0)
		self.assertEqual(frappe.db.get_value("Journal Entry", original.name, "modified"), original.modified)
		self.assertEqual(frappe.db.get_value("Journal Entry", original.name, "docstatus"), 1)

	def test_cannot_reverse_twice(self):
		jan = self.post("2024-01-01")
		jan.reverse_provision("2024-02-05")
		with self.assertRaisesRegex(frappe.ValidationError, "cannot be reversed"):
			frappe.get_doc("Gratuity Accrual", jan.name).reverse_provision("2024-02-06")
		self.assertEqual(
			frappe.db.count(
				"Journal Entry", {"gratuity_accrual": jan.name, "gratuity_entry_type": "Provision Reversal"}
			),
			1,
		)

	def test_reversal_blocked_when_later_accrual_posted(self):
		jan = self.post("2024-01-01")
		self.post("2024-02-01")
		with self.assertRaisesRegex(frappe.ValidationError, "later posted"):
			jan.reverse_provision("2024-03-05")

	def test_recalculation_after_reversal(self):
		jan = self.post("2024-01-01")
		reversal = jan.reverse_provision("2024-02-05")
		jan.reload()

		recalculation = frappe.get_doc("Gratuity Accrual", jan.create_recalculation())
		self.assertEqual(recalculation.recalculation_of, jan.name)
		self.assertEqual(str(recalculation.posting_date), "2024-02-05")
		with self.assertRaisesRegex(frappe.ValidationError, "already exists"):
			jan.create_recalculation()

		recalculation.submit()
		self.assertFalse(recalculation.opening_reversal_journal_entry)  # balance was 0 after reversal
		self.assertEqual(
			account_total(je_lines(recalculation.provision_journal_entry), self.accounts.provision, "credit"),
			10500,
		)
		self.assertEqual(self.balance(self.senior), 10500)

		# full history retained
		self.assertEqual(frappe.db.get_value("Gratuity Accrual", jan.name, "status"), "Reversed")
		self.assertEqual(frappe.db.get_value("Journal Entry", reversal, "docstatus"), 1)
		self.assertEqual(frappe.db.get_value("Journal Entry", jan.provision_journal_entry, "docstatus"), 1)

	def test_recalculation_does_not_inherit_source_state(self):
		"""Regression (UAT HR-GRA-2026-00003): a recalculation inherited the source's status and JEs."""
		source = self.post("2024-02-01", employees=[self.senior])
		self.assertFalse(source.opening_reversal_journal_entry)
		source_reversal = source.reverse_provision("2024-03-05")
		source.reload()
		source_provision = source.provision_journal_entry

		# --- before submit: a fresh draft, linked to the source, with no accounting state ---
		name = source.create_recalculation()
		recalculation = frappe.get_doc("Gratuity Accrual", name)
		self.assertEqual((recalculation.docstatus, recalculation.status), (0, "Draft"))
		self.assertEqual(recalculation.recalculation_of, source.name)
		self.assertFalse(recalculation.opening_reversal_journal_entry)
		self.assertFalse(recalculation.provision_journal_entry)
		self.assertFalse(recalculation.reversal_journal_entry)
		# business inputs are carried over
		for fieldname in (
			"company",
			"gratuity_rule",
			"from_date",
			"to_date",
			"expense_account",
			"expense_reversal_account",
			"provision_account",
			"cost_center",
		):
			self.assertEqual(recalculation.get(fieldname), source.get(fieldname), fieldname)
		self.assertEqual([r.employee for r in recalculation.employees], [self.senior])
		self.assertEqual(recalculation.employees[0].previous_provision, 0)

		# --- after submit with previous provision = 0: Posted, only a new Provision JE ---
		recalculation.submit()
		recalculation.reload()
		self.assertEqual((recalculation.docstatus, recalculation.status), (1, "Posted"))
		self.assertEqual(recalculation.recalculation_of, source.name)
		self.assertFalse(recalculation.opening_reversal_journal_entry)
		self.assertFalse(recalculation.reversal_journal_entry)
		self.assertTrue(recalculation.provision_journal_entry)
		self.assertNotIn(recalculation.provision_journal_entry, (source_provision, source_reversal))
		self.assertEqual(
			frappe.get_all(
				"Journal Entry",
				filters={"gratuity_accrual": recalculation.name, "docstatus": 1},
				pluck="gratuity_entry_type",
			),
			["Provision"],
		)
		# the activity log never shows the source's Journal Entries on the recalculation
		versions = frappe.get_all(
			"Version",
			filters={"ref_doctype": "Gratuity Accrual", "docname": recalculation.name},
			pluck="data",
		)
		self.assertFalse([v for v in versions if source_reversal in v or source_provision in v])

		# --- the source keeps its own reversal ---
		source.reload()
		self.assertEqual((source.status, source.reversal_journal_entry), ("Reversed", source_reversal))
		self.assertEqual(source.provision_journal_entry, source_provision)

		# --- the recalculation gets a Reversal JE only when it is itself reversed ---
		own_reversal = recalculation.reverse_provision("2024-03-10")
		recalculation.reload()
		self.assertEqual(
			(recalculation.status, recalculation.reversal_journal_entry), ("Reversed", own_reversal)
		)
		self.assertNotEqual(own_reversal, source_reversal)
		self.assertEqual(
			frappe.db.get_value("Journal Entry", own_reversal, "reversal_of"),
			recalculation.provision_journal_entry,
		)

	def test_recalculation_requires_reversed_original(self):
		jan = self.post("2024-01-01")
		with self.assertRaisesRegex(frappe.ValidationError, "reversed"):
			jan.create_recalculation()

	def test_posting_date_before_existing_provision_blocked(self):
		jan = self.post("2024-01-01")
		jan.reverse_provision("2024-02-05")
		with self.assertRaisesRegex(frappe.ValidationError, "after this Posting Date"):
			make_accrual(self.company, "2024-01-01", employees=[self.senior])

	def test_posted_accrual_cannot_be_cancelled(self):
		jan = self.post("2024-01-01")
		with self.assertRaisesRegex(frappe.ValidationError, "Reverse Provision"):
			jan.cancel()

	def test_gratuity_journal_entries_guarded(self):
		from erpnext.accounts.doctype.journal_entry.journal_entry import make_reverse_journal_entry

		jan = self.post("2024-01-01")
		manual_reversal = make_reverse_journal_entry(jan.provision_journal_entry)
		manual_reversal.posting_date = "2024-02-05"
		with self.assertRaisesRegex(frappe.ValidationError, "Use Reverse Provision"):
			manual_reversal.insert()

		with self.assertRaisesRegex(frappe.ValidationError, "posted by"):
			frappe.get_doc("Journal Entry", jan.provision_journal_entry).cancel()

		forged = frappe.copy_doc(frappe.get_doc("Journal Entry", jan.provision_journal_entry))
		forged.docstatus = 0  # copy_doc keeps docstatus under tests; a UI duplicate starts as draft
		forged.gratuity_entry_type = "Provision"
		with self.assertRaisesRegex(frappe.ValidationError, "created only by"):
			forged.insert()

	def test_calculation_error_blocks_submit(self):
		no_slip = make_employee(self.company, "2019-02-01", first_name="_TG No Slip")
		accrual = make_accrual(self.company, "2024-01-01", employees=[no_slip])
		self.assertIn("No submitted Salary Slip", accrual.employees[0].calculation_error)
		with self.assertRaisesRegex(frappe.ValidationError, "No submitted Salary Slip"):
			accrual.submit()

	def test_get_employees_includes_balance_carriers(self):
		self.post("2024-01-01")
		# relieved before the next period but provision never released (no Gratuity yet)
		frappe.db.set_value("Employee", self.senior, {"status": "Left", "relieving_date": "2024-01-31"})
		feb = make_accrual(self.company, "2024-02-01")
		row = next(r for r in feb.employees if r.employee == self.senior)
		self.assertEqual(str(row.as_of_date), "2024-01-31")
		self.assertEqual((row.previous_provision, row.liability), (10500, 10500))

	# --- manual reversal semantics -------------------------------------------------------------------

	def test_reversal_of_link_and_cancellation_protection(self):
		jan = self.post("2024-01-01")
		reversal = frappe.get_doc("Journal Entry", jan.reverse_provision("2024-02-05"))

		# standard traceability to the one specific entry being reversed
		self.assertEqual(reversal.reversal_of, jan.provision_journal_entry)
		self.assertEqual(reversal.total_debit, reversal.total_credit)
		self.assertEqual(
			{(r.account, r.party, r.debit, r.credit) for r in reversal.accounts},
			{
				(self.accounts.provision, self.senior, 10500, 0),
				(self.accounts.reversal, None, 0, 10500),
			},
		)

		# the original cannot be cancelled: first by this app's guard...
		original = frappe.get_doc("Journal Entry", jan.provision_journal_entry)
		with self.assertRaisesRegex(frappe.ValidationError, "posted by"):
			original.cancel()
		# ...and independently by ERPNext's standard link check on `reversal_of` (guard bypassed here)
		original = frappe.get_doc("Journal Entry", jan.provision_journal_entry)
		original.flags.gratuity_custom_managed = True
		with self.assertFailsAndRollsBack("linked with"):
			original.cancel()
		self.assertEqual(frappe.db.get_value("Journal Entry", original.name, "docstatus"), 1)
		# the reversal itself cannot be cancelled outside Gratuity Accrual
		with self.assertRaisesRegex(frappe.ValidationError, "posted by"):
			reversal.cancel()
		# nor reversed again through the standard Journal Entry action
		from erpnext.accounts.doctype.journal_entry.journal_entry import make_reverse_journal_entry

		reverse_of_reversal = make_reverse_journal_entry(reversal.name)
		reverse_of_reversal.posting_date = "2024-02-06"
		with self.assertRaisesRegex(frappe.ValidationError, "Use Reverse Provision"):
			reverse_of_reversal.insert()

	def test_reversal_when_expense_and_reversal_accounts_are_the_same(self):
		jan = self.post("2024-01-01", expense_reversal_account=self.accounts.expense)
		reversal = frappe.get_doc("Journal Entry", jan.reverse_provision("2024-02-05"))
		self.assertEqual(
			{(r.account, r.debit, r.credit) for r in reversal.accounts},
			{(self.accounts.provision, 10500, 0), (self.accounts.expense, 0, 10500)},
		)

	# --- transaction safety: a failure at any step leaves nothing behind ------------------------------

	def test_failed_submit_leaves_no_journal_entry(self):
		from unittest.mock import patch

		from gratuity_custom.gratuity_custom.doctype.gratuity_accrual.gratuity_accrual import GratuityAccrual

		self.post("2024-01-01")
		feb = make_accrual(self.company, "2024-02-01", employees=[self.senior])
		# fail after the Opening Reversal JE has already been created and submitted
		with patch.object(GratuityAccrual, "post_provision", side_effect=frappe.ValidationError("boom")):
			with self.assertFailsAndRollsBack("boom"):
				feb.submit()

		self.assertEqual(
			frappe.db.get_value("Gratuity Accrual", feb.name, ["docstatus", "status"]), (0, "Draft")
		)
		self.assertFalse(frappe.db.exists("Journal Entry", {"gratuity_accrual": feb.name}))
		self.assertEqual(self.balance(self.senior), 10500)

	def test_failed_reversal_leaves_no_journal_entry(self):
		from unittest.mock import patch

		from gratuity_custom.gratuity_custom.doctype.gratuity_accrual.gratuity_accrual import GratuityAccrual

		jan = self.post("2024-01-01")
		# fail after the reversal JE has been submitted, before the status is updated
		with patch.object(GratuityAccrual, "set_status", side_effect=frappe.ValidationError("boom")):
			with self.assertFailsAndRollsBack("boom"):
				jan.reverse_provision("2024-02-05")

		self.assertEqual(
			frappe.db.get_value("Gratuity Accrual", jan.name, ["status", "reversal_journal_entry"]),
			("Posted", None),
		)
		self.assertFalse(frappe.db.exists("Journal Entry", {"reversal_of": jan.provision_journal_entry}))
		self.assertEqual(self.balance(self.senior), 10500)

	# --- period safety ---------------------------------------------------------------------------------

	def test_recalculation_link_cannot_be_forged(self):
		jan = self.post("2024-01-01")
		with self.assertRaisesRegex(frappe.ValidationError, "reversed before it can be recalculated"):
			make_accrual(self.company, "2024-01-01", employees=[self.senior], recalculation_of=jan.name)

		jan.reverse_provision("2024-02-05")
		with self.assertRaisesRegex(frappe.ValidationError, "keep the company and period"):
			make_accrual(
				self.company,
				"2024-02-01",
				employees=[self.senior],
				recalculation_of=jan.name,
				posting_date="2024-02-29",
			)
