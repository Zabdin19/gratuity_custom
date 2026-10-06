import frappe
from frappe.utils import flt
from hrms.overrides.employee_payment_entry import get_payment_entry_for_employee

from gratuity_custom.tests.utils import (
	GratuityTestCase,
	make_employee_with_salary,
	make_gratuity,
	setup_gratuity_company,
)

RELIEVING_DATE = "2024-03-14"
STANDARD_COMPONENTS = {"Expense Claim", "Bonus", "Leave Encashment", "Employee Advance"}


class TestFullAndFinalGratuity(GratuityTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.setup = setup_gratuity_company()
		cls.company, cls.accounts = cls.setup.company, cls.setup.accounts
		cls.employee = make_employee_with_salary(
			cls.company, "2019-02-01", 3000, ["2024-01-15"], first_name="_TG FnF"
		)
		frappe.db.set_value("Employee", cls.employee, {"status": "Left", "relieving_date": RELIEVING_DATE})
		cls.cash_account = frappe.db.get_value(
			"Account", {"company": cls.company, "account_type": "Cash", "is_group": 0}
		)

	def setUp(self):
		super().setUp()
		self.gratuity = make_gratuity(
			self.company, self.employee, self.setup.rule, self.accounts.payable, RELIEVING_DATE
		)
		self.assertEqual(self.gratuity.status, "Unpaid")

	# --- helpers ----------------------------------------------------------------------------------

	def make_statement(self):
		return frappe.get_doc(
			{
				"doctype": "Full and Final Statement",
				"employee": self.employee,
				"transaction_date": RELIEVING_DATE,
			}
		).insert()

	def gratuity_rows(self, fnf):
		return [r for r in fnf.payables if r.reference_document_type == "Gratuity"]

	def stored_status(self, fnf):
		return frappe.db.get_value(
			"Full and Final Outstanding Statement",
			{"parent": fnf.name, "reference_document": self.gratuity.name},
			"status",
		)

	def pay_gratuity(self):
		pe = get_payment_entry_for_employee("Gratuity", self.gratuity.name, bank_account=self.cash_account)
		pe.posting_date = RELIEVING_DATE
		pe.reference_no, pe.reference_date = "GRATUITY-PAY", RELIEVING_DATE
		pe.insert()
		pe.submit()
		self.assertEqual(frappe.db.get_value("Gratuity", self.gratuity.name, "status"), "Paid")
		return pe

	def assert_gratuity_row(self, fnf, status):
		rows = self.gratuity_rows(fnf)
		self.assertEqual(len(rows), 1)
		row = rows[0]
		self.assertEqual(
			(row.component, row.reference_document, row.amount, row.account, row.status),
			("Gratuity", self.gratuity.name, self.gratuity.amount, self.accounts.payable, status),
		)

	# --- tests --------------------------------------------------------------------------------------

	def test_unpaid_gratuity_fetched_unsettled(self):
		fnf = self.make_statement()
		self.assert_gratuity_row(fnf, "Unsettled")
		self.assertEqual(fnf.total_payable_amount, self.gratuity.amount)

	def test_paid_gratuity_fetched_settled(self):
		self.pay_gratuity()
		fnf = self.make_statement()
		self.assert_gratuity_row(fnf, "Settled")

	def test_payment_after_statement_settles_row(self):
		fnf = self.make_statement()
		self.assertEqual(self.stored_status(fnf), "Unsettled")

		self.pay_gratuity()
		self.assertEqual(self.stored_status(fnf), "Settled")  # updated by the Payment Entry hook

		# a save/refresh also syncs it (server side), even if the stored row were stale
		frappe.db.set_value(
			"Full and Final Outstanding Statement", self.gratuity_rows(fnf)[0].name, "status", "Unsettled"
		)
		fnf = frappe.get_doc("Full and Final Statement", fnf.name)
		fnf.save()
		self.assert_gratuity_row(fnf, "Settled")

	def test_payment_cancel_unsettles_row(self):
		fnf = self.make_statement()
		pe = self.pay_gratuity()
		self.assertEqual(self.stored_status(fnf), "Settled")

		pe.cancel()
		self.assertEqual(frappe.db.get_value("Gratuity", self.gratuity.name, "status"), "Unpaid")
		self.assertEqual(self.stored_status(fnf), "Unsettled")
		fnf = frappe.get_doc("Full and Final Statement", fnf.name)
		fnf.save()
		self.assert_gratuity_row(fnf, "Unsettled")

	def test_manual_selection_syncs_status(self):
		"""The reported UAT case: placeholder row, reference picked by hand, Gratuity already paid."""
		self.pay_gratuity()
		fnf = frappe.new_doc("Full and Final Statement")
		# the form fetches company / relieving date from the employee before asking for statements
		fnf.update(
			{
				"employee": self.employee,
				"company": self.company,
				"relieving_date": RELIEVING_DATE,
				"transaction_date": RELIEVING_DATE,
			}
		)
		fnf.get_outstanding_statements()  # what the form does when the employee is chosen
		row = self.gratuity_rows(fnf)[0]
		row.reference_document, row.amount, row.status = self.gratuity.name, 0, "Unsettled"
		fnf.insert()
		fnf.reload()
		self.assert_gratuity_row(fnf, "Settled")

	def test_no_duplicate_gratuity_row(self):
		fnf = self.make_statement()
		fnf.save()
		self.assertEqual(len(self.gratuity_rows(fnf)), 1)

		fnf.append(
			"payables",
			{
				"component": "Gratuity",
				"reference_document_type": "Gratuity",
				"reference_document": self.gratuity.name,
			},
		)
		with self.assertRaisesRegex(frappe.ValidationError, "already included"):
			fnf.save()

		# a Gratuity already on another non-cancelled statement is not fetched again
		second = self.make_statement()
		self.assertFalse([r for r in self.gratuity_rows(second) if r.reference_document])

	def test_unpaid_gratuity_can_still_be_settled_through_statement(self):
		"""Standard HRMS flow: mark Settled, submit, and the statement's Journal Entry pays it."""
		fnf = self.make_statement()
		for row in fnf.payables + fnf.receivables:
			row.status = "Settled"
		fnf.save()
		self.assert_gratuity_row(fnf, "Settled")  # not forced back to Unsettled
		fnf.submit()
		self.assertEqual(fnf.docstatus, 1)

	def test_other_components_unaffected(self):
		fnf = self.make_statement()
		others = [r for r in fnf.payables + fnf.receivables if r.reference_document_type != "Gratuity"]
		self.assertEqual({r.component for r in others}, STANDARD_COMPONENTS)
		self.assertTrue(
			all(r.status == "Unsettled" and not r.amount and not r.reference_document for r in others)
		)

		self.pay_gratuity()
		fnf = frappe.get_doc("Full and Final Statement", fnf.name)
		bonus = next(r for r in fnf.payables if r.component == "Bonus")
		bonus.amount = 500
		fnf.save()
		fnf.reload()
		others = {
			r.component: (r.status, r.amount)
			for r in fnf.payables + fnf.receivables
			if r.reference_document_type != "Gratuity"
		}
		self.assertEqual(others["Bonus"], ("Unsettled", 500))
		self.assertTrue(all(status == "Unsettled" for status, _amount in others.values()))
		self.assertEqual(fnf.total_payable_amount, self.gratuity.amount + 500)

	# --- statement Journal Entry: no double payment of an already-paid Gratuity ----------------------

	BONUS, ADVANCE = 500, 200

	def submit_statement(self):
		"""Statement with the Gratuity row plus a Bonus payable and an Employee Advance receivable."""
		fnf = self.make_statement()
		company = self.company
		for row in fnf.payables + fnf.receivables:
			if row.component == "Bonus":
				row.amount = self.BONUS
				row.account = frappe.db.get_value(
					"Account", {"company": company, "account_name": "Payroll Payable"}
				)
			elif row.component == "Employee Advance":
				row.amount = self.ADVANCE
				row.account = frappe.db.get_value(
					"Account", {"company": company, "account_name": "Employee Advances"}
				)
			row.status = "Settled"  # standard flow: settle every row, then submit
		# remove unused placeholders: HRMS loads every Leave Encashment row when the Journal Entry is submitted
		used = ("Gratuity", "Bonus", "Employee Advance")
		fnf.set("payables", [r for r in fnf.payables if r.component in used])
		fnf.set("receivables", [r for r in fnf.receivables if r.component in used])
		fnf.save()
		fnf.submit()
		return fnf

	def settlement_journal_entry(self, fnf):
		"""Exactly what the statement's standard "Create Journal Entry" button builds."""
		jv = fnf.create_journal_entry()
		jv.posting_date = RELIEVING_DATE
		jv.cheque_no, jv.cheque_date = f"FNF-{fnf.name}", RELIEVING_DATE
		jv.accounts[-1].account = self.cash_account
		return jv

	def lines(self, jv):
		return sorted(
			(r.account, r.party or "", flt(r.debit_in_account_currency), flt(r.credit_in_account_currency))
			for r in jv.accounts
		)

	def bonus_and_advance_lines(self, fnf, cash_credit):
		payroll = next(r.account for r in fnf.payables if r.component == "Bonus")
		advances = next(r.account for r in fnf.receivables if r.component == "Employee Advance")
		return [
			(payroll, "", self.BONUS, 0),
			(advances, self.employee, 0, self.ADVANCE),
			(self.cash_account, "", 0, cash_credit),
		]

	def assert_blocked(self, jv):
		with self.assertFailsAndRollsBack(f"Gratuity Already Paid|{self.gratuity.name}.*already paid"):
			jv.insert()

	def test_unpaid_gratuity_paid_through_statement_journal_entry(self):
		fnf = self.submit_statement()
		jv = self.settlement_journal_entry(fnf)
		jv.insert()
		jv.submit()

		amount = self.gratuity.amount
		self.assertEqual(
			self.lines(jv),
			sorted(
				[
					(self.accounts.payable, self.employee, amount, 0),
					*self.bonus_and_advance_lines(fnf, amount + self.BONUS - self.ADVANCE),
				]
			),
		)
		self.assertEqual(
			frappe.db.get_value("Gratuity", self.gratuity.name, ["status", "paid_amount"]), ("Paid", amount)
		)
		self.assertEqual(frappe.db.get_value("Full and Final Statement", fnf.name, "status"), "Paid")

	def test_paid_gratuity_not_paid_again_by_statement(self):
		self.pay_gratuity()
		fnf = self.submit_statement()
		jv = self.settlement_journal_entry(fnf)
		# the standard builder still includes the full Gratuity row amount...
		self.assertIn((self.accounts.payable, self.employee, self.gratuity.amount, 0), self.lines(jv))
		# ...so saving it is blocked, naming the paid Gratuity
		self.assert_blocked(jv)
		self.assertFalse(
			frappe.db.exists("Journal Entry Account", {"reference_name": fnf.name, "docstatus": 1})
		)
		self.assertEqual(
			frappe.db.get_value("Gratuity", self.gratuity.name, "paid_amount"), self.gratuity.amount
		)

		# remedy from the message: drop the Gratuity line and reduce the payment line accordingly
		jv = self.settlement_journal_entry(fnf)
		jv.accounts = [r for r in jv.accounts if r.account != self.accounts.payable]
		jv.accounts[-1].credit_in_account_currency = self.BONUS - self.ADVANCE
		jv.insert()
		jv.submit()
		# other payable / receivable components are paid normally
		self.assertEqual(self.lines(jv), sorted(self.bonus_and_advance_lines(fnf, self.BONUS - self.ADVANCE)))
		self.assertEqual(
			frappe.db.get_value("Gratuity", self.gratuity.name, "paid_amount"), self.gratuity.amount
		)
		self.assertEqual(frappe.db.get_value("Full and Final Statement", fnf.name, "status"), "Paid")

	def test_external_payment_after_statement_blocks_duplicate(self):
		fnf = self.submit_statement()  # Gratuity still unpaid at submission
		self.pay_gratuity()  # then paid outside the statement
		self.assert_blocked(self.settlement_journal_entry(fnf))

	def test_cancelled_external_payment_reopens_statement_payment(self):
		fnf = self.submit_statement()
		pe = self.pay_gratuity()
		self.assert_blocked(self.settlement_journal_entry(fnf))

		pe.cancel()
		self.assertEqual(
			frappe.db.get_value("Gratuity", self.gratuity.name, ["status", "paid_amount"]), ("Unpaid", 0)
		)
		jv = self.settlement_journal_entry(fnf)
		jv.insert()
		jv.submit()
		self.assertIn((self.accounts.payable, self.employee, self.gratuity.amount, 0), self.lines(jv))
		self.assertEqual(frappe.db.get_value("Gratuity", self.gratuity.name, "status"), "Paid")

	def test_unrelated_journal_entries_unaffected(self):
		"""A Journal Entry that does not reference a statement is never checked."""
		self.pay_gratuity()
		jv = frappe.get_doc(
			{
				"doctype": "Journal Entry",
				"company": self.company,
				"posting_date": RELIEVING_DATE,
				"accounts": [
					{
						"account": self.accounts.payable,
						"party_type": "Employee",
						"party": self.employee,
						"debit_in_account_currency": 100,
					},
					{"account": self.cash_account, "credit_in_account_currency": 100},
				],
			}
		).insert()
		self.assertEqual(jv.docstatus, 0)
