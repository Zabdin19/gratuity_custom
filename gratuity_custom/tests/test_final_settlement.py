import frappe
from frappe.query_builder.functions import Sum

from gratuity_custom.accounts import get_provision_balances
from gratuity_custom.calculation import calculate_gratuity_liability
from gratuity_custom.tests.utils import (
	BASIC_COMPONENT,
	GratuityTestCase,
	make_accrual,
	make_employee_with_salary,
	make_gratuity,
	make_gratuity_rule,
	settle_through_full_and_final,
	setup_gratuity_company,
)

RELIEVING_DATE = "2024-03-14"  # resigns in the middle of March


class TestFinalSettlement(GratuityTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.setup = setup_gratuity_company()
		cls.company, cls.accounts, cls.rule = cls.setup.company, cls.setup.accounts, cls.setup.rule
		cls.employee = make_employee_with_salary(
			cls.company, "2019-02-01", 3000, ["2024-01-15"], first_name="_TG Leaver"
		)

	def setUp(self):
		super().setUp()
		# January and February provisions are posted before the employee resigns
		for month in ("2024-01-01", "2024-02-01"):
			make_accrual(self.company, month, employees=[self.employee], submit=True)
		frappe.db.set_value("Employee", self.employee, {"status": "Left", "relieving_date": RELIEVING_DATE})

	def provision_balance(self, as_of="2099-12-31"):
		return get_provision_balances(self.company, self.accounts.provision, [self.employee], as_of).get(
			self.employee, 0
		)

	def gl_net(self, accounts, party=None):
		gle = frappe.qb.DocType("GL Entry")
		query = (
			frappe.qb.from_(gle)
			.select(Sum(gle.debit - gle.credit))
			.where((gle.company == self.company) & (gle.account.isin(accounts)) & (gle.is_cancelled == 0))
		)
		if party:
			query = query.where(gle.party == party)
		return query.run()[0][0] or 0

	def new_leaver(self, first_name):
		"""Employee who resigns on RELIEVING_DATE and has no accruals of their own."""
		employee = make_employee_with_salary(
			self.company, "2019-02-01", 3000, ["2024-01-15"], first_name=first_name
		)
		frappe.db.set_value("Employee", employee, {"status": "Left", "relieving_date": RELIEVING_DATE})
		return employee

	def clear_company_configuration(self):
		frappe.db.set_value(
			"Company",
			self.company,
			{
				"gratuity_expense_account": None,
				"gratuity_expense_reversal_account": None,
				"gratuity_provision_account": None,
				"default_gratuity_rule": None,
			},
		)

	def release_entries(self, gratuity):
		return frappe.get_all(
			"Journal Entry",
			filters={"gratuity": gratuity, "gratuity_entry_type": "Provision Release", "docstatus": 1},
			pluck="name",
		)

	def assert_release(self, gratuity, employee, amount):
		"""Exactly one release JE: Dr Provision (party = employee) / Cr Expense Reversal, at the Gratuity date."""
		self.assertEqual(self.release_entries(gratuity.name), [gratuity.provision_release_journal_entry])
		release = frappe.get_doc("Journal Entry", gratuity.provision_release_journal_entry)
		self.assertEqual(str(release.posting_date), str(gratuity.posting_date))
		self.assertEqual(
			(release.gratuity, release.gratuity_entry_type), (gratuity.name, "Provision Release")
		)
		self.assertEqual(
			sorted(
				(r.account, r.party_type or "", r.party or "", r.debit, r.credit) for r in release.accounts
			),
			sorted(
				[
					(self.accounts.provision, "Employee", employee, amount, 0),
					(self.accounts.reversal, "", "", 0, amount),
				]
			),
		)
		balance = get_provision_balances(self.company, self.accounts.provision, [employee], "2099-12-31")
		self.assertEqual(balance.get(employee, 0), 0)
		# the standard Gratuity GL is unchanged: Dr Expense / Cr Payable for the Gratuity amount
		gl = frappe.get_all(
			"GL Entry",
			filters={"voucher_type": "Gratuity", "voucher_no": gratuity.name, "is_cancelled": 0},
			fields=["account", "debit", "credit"],
		)
		self.assertEqual(
			sorted((g.account, g.debit, g.credit) for g in gl),
			sorted(
				[(self.accounts.expense, gratuity.amount, 0), (self.accounts.payable, 0, gratuity.amount)]
			),
		)

	def make_gratuity(self, posting_date=RELIEVING_DATE, submit=True, employee=None, **kwargs):
		return make_gratuity(
			self.company,
			employee or self.employee,
			self.rule,
			self.accounts.payable,
			posting_date,
			submit=submit,
			**kwargs,
		)

	def test_mid_month_final_settlement_releases_provision(self):
		feb_provision = calculate_gratuity_liability(self.employee, self.rule, "2024-02-29").liability
		final = calculate_gratuity_liability(self.employee, self.rule, RELIEVING_DATE)
		self.assertEqual(self.provision_balance(), feb_provision)

		gratuity = self.make_gratuity()

		# standard HRMS amount = days-based liability up to the relieving date
		self.assertEqual(gratuity.amount, final.liability)
		self.assertGreater(gratuity.amount, feb_provision)
		self.assertEqual(gratuity.expense_account, self.accounts.expense)

		release = frappe.get_doc("Journal Entry", gratuity.provision_release_journal_entry)
		self.assertEqual(
			(release.gratuity_entry_type, release.gratuity), ("Provision Release", gratuity.name)
		)
		lines = {(r.account, r.party): (r.debit, r.credit) for r in release.accounts}
		self.assertEqual(lines[(self.accounts.provision, self.employee)], (feb_provision, 0))
		self.assertEqual(lines[(self.accounts.reversal, None)], (0, feb_provision))

		# no double liability, no double expense
		self.assertEqual(self.provision_balance(), 0)
		self.assertEqual(self.gl_net([self.accounts.payable], self.employee), -gratuity.amount)
		self.assertEqual(self.gl_net([self.accounts.expense, self.accounts.reversal]), gratuity.amount)

	def test_final_month_accrual_then_settlement(self):
		"""An accrual covering the exit month is capped at the relieving date: release == gratuity."""
		march = make_accrual(self.company, "2024-03-01", employees=[self.employee], submit=True)
		self.assertEqual(str(march.employees[0].as_of_date), RELIEVING_DATE)

		with self.assertFailsAndRollsBack("after this Posting Date"):
			self.make_gratuity(posting_date="2024-03-20")

		gratuity = self.make_gratuity(posting_date="2024-03-31")
		release = frappe.get_doc("Journal Entry", gratuity.provision_release_journal_entry)
		self.assertEqual(release.total_debit, gratuity.amount)
		self.assertEqual(self.provision_balance(), 0)
		self.assertEqual(self.gl_net([self.accounts.expense, self.accounts.reversal]), gratuity.amount)

	def test_pay_via_salary_slip_rejected_not_changed(self):
		doc = frappe.get_doc(
			{
				"doctype": "Gratuity",
				"employee": self.employee,
				"company": self.company,
				"posting_date": RELIEVING_DATE,
				"gratuity_rule": self.rule,
				"pay_via_salary_slip": 1,
				"salary_component": BASIC_COMPONENT,
				"payroll_date": RELIEVING_DATE,
				"expense_account": self.accounts.expense,
				"payable_account": self.accounts.payable,
			}
		)
		with self.assertRaisesRegex(frappe.ValidationError, "Uncheck .*Pay via Salary Slip"):
			doc.insert()
		self.assertEqual(doc.pay_via_salary_slip, 1)  # validation only, never silently flipped

	def test_missing_expense_account_rejected_not_defaulted(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Please set .*Expense Account"):
			self.make_gratuity(expense_account=None)

	def test_expense_account_must_be_expense(self):
		with self.assertRaisesRegex(frappe.ValidationError, "must be an Expense account"):
			self.make_gratuity(expense_account=self.accounts.provision)

	def test_cancel_gratuity_cancels_release(self):
		balance = self.provision_balance()
		gratuity = self.make_gratuity()
		release = gratuity.provision_release_journal_entry

		gratuity.cancel()
		self.assertEqual(frappe.db.get_value("Journal Entry", release, "docstatus"), 2)
		self.assertEqual(self.provision_balance(), balance)
		self.assertEqual(self.gl_net([self.accounts.payable], self.employee), 0)

	def test_release_entry_cannot_be_cancelled_directly(self):
		gratuity = self.make_gratuity()
		with self.assertRaisesRegex(frappe.ValidationError, "posted by"):
			frappe.get_doc("Journal Entry", gratuity.provision_release_journal_entry).cancel()

	def test_no_accrual_after_settlement(self):
		self.make_gratuity()
		with self.assertRaisesRegex(frappe.ValidationError, "already been settled"):
			make_accrual(self.company, "2024-03-01", employees=[self.employee])
		accrual = make_accrual(self.company, "2024-03-01", employees=None)
		self.assertNotIn(self.employee, [row.employee for row in accrual.employees])

	def test_full_and_final_statement_settles_gratuity(self):
		gratuity = self.make_gratuity()

		fnf, _jv = settle_through_full_and_final(gratuity, "2024-03-20")
		self.assertEqual(fnf.total_payable_amount, gratuity.amount)

		self.assertEqual(frappe.db.get_value("Full and Final Statement", fnf.name, "status"), "Paid")
		self.assertEqual(frappe.db.get_value("Gratuity", gratuity.name, "status"), "Paid")
		self.assertEqual(self.gl_net([self.accounts.payable], self.employee), 0)
		self.assertEqual(self.provision_balance(), 0)

	def test_company_without_provisioning_stays_standard(self):
		employee = self.new_leaver("_TG Standard Leaver")  # no accruals
		self.clear_company_configuration()
		gratuity = self.make_gratuity(
			employee=employee,
			pay_via_salary_slip=1,
			salary_component=BASIC_COMPONENT,
			payroll_date=RELIEVING_DATE,
		)
		self.assertFalse(gratuity.provision_release_journal_entry)
		self.assertFalse(self.release_entries(gratuity.name))
		self.assertTrue(
			frappe.db.exists("Additional Salary", {"ref_doctype": "Gratuity", "ref_docname": gratuity.name})
		)

	# --- Gratuity Rule consistency (provisioning companies only) -----------------------------------

	def test_exact_default_rule_accepted(self):
		self.assertEqual(frappe.db.get_value("Company", self.company, "default_gratuity_rule"), self.rule)
		gratuity = self.make_gratuity()
		self.assertEqual((gratuity.docstatus, gratuity.gratuity_rule), (1, self.rule))

	def test_round_rule_rejected(self):
		rounding = make_gratuity_rule("_Test Gratuity Rule Round", method="Round off Work Experience")
		doc = self.make_gratuity(submit=False, gratuity_rule=self.rule)
		doc.gratuity_rule = rounding
		with self.assertRaisesRegex(frappe.ValidationError, "Take Exact Completed Years"):
			doc.save()
		self.assertEqual(doc.gratuity_rule, rounding)  # not silently replaced

	def test_rule_different_from_company_default_rejected(self):
		other = make_gratuity_rule("_Test Gratuity Rule Other Exact")
		with self.assertRaisesRegex(frappe.ValidationError, "Default Gratuity Rule"):
			self.make_gratuity(gratuity_rule=other)

	def test_non_provisioning_company_rule_unaffected(self):
		employee = self.new_leaver("_TG Standard Leaver")  # no accruals
		self.clear_company_configuration()
		rounding = make_gratuity_rule("_Test Gratuity Rule Round", method="Round off Work Experience")
		gratuity = self.make_gratuity(
			employee=employee,
			gratuity_rule=rounding,
			pay_via_salary_slip=1,
			salary_component=BASIC_COMPONENT,
			payroll_date=RELIEVING_DATE,
		)
		self.assertEqual((gratuity.docstatus, gratuity.gratuity_rule), (1, rounding))
		self.assertFalse(gratuity.provision_release_journal_entry)

	# --- provision release regression (UAT HR-GRA-PAY-00001) ---------------------------------------

	def test_normal_posted_accrual_released(self):
		live = self.provision_balance()
		self.assertGreater(live, 0)
		gratuity = self.make_gratuity()
		self.assert_release(gratuity, self.employee, live)

	def test_posted_recalculation_released(self):
		"""Source accrual reversed, latest recalculation Posted: the recalculated provision is released."""
		feb = frappe.get_doc(
			"Gratuity Accrual",
			frappe.db.get_value("Gratuity Accrual", {"from_date": "2024-02-01", "docstatus": 1}),
		)
		feb.reverse_provision("2024-03-05")
		feb.reload()
		recalculation = frappe.get_doc("Gratuity Accrual", feb.create_recalculation())
		recalculation.submit()
		self.assertEqual(frappe.db.get_value("Gratuity Accrual", feb.name, "status"), "Reversed")
		self.assertEqual(frappe.db.get_value("Gratuity Accrual", recalculation.name, "status"), "Posted")

		live = self.provision_balance()
		self.assertEqual(live, recalculation.employees[0].liability)

		gratuity = self.make_gratuity()
		self.assert_release(gratuity, self.employee, live)

	def test_release_when_company_not_configured(self):
		"""UAT reproduction: accruals posted with accounts entered on the accrual, Company not configured."""
		employee = self.new_leaver("_TG Unconfigured Leaver")
		self.clear_company_configuration()
		accounts = {
			"gratuity_rule": self.rule,
			"expense_account": self.accounts.expense,
			"expense_reversal_account": self.accounts.reversal,
			"provision_account": self.accounts.provision,
			"cost_center": frappe.db.get_value("Company", self.company, "cost_center"),
		}
		make_accrual(self.company, "2024-01-01", employees=[employee], submit=True, **accounts)
		feb = make_accrual(self.company, "2024-02-01", employees=[employee], submit=True, **accounts)
		feb.reverse_provision("2024-03-05")
		feb.reload()
		frappe.get_doc("Gratuity Accrual", feb.create_recalculation()).submit()

		live = get_provision_balances(self.company, self.accounts.provision, [employee], RELIEVING_DATE)[
			employee
		]
		self.assertGreater(live, 0)

		gratuity = self.make_gratuity(employee=employee, expense_account=self.accounts.expense)
		self.assert_release(gratuity, employee, live)

	def test_release_not_duplicated_on_retry(self):
		from gratuity_custom.events.gratuity import release_provision_for_gratuity

		gratuity = self.make_gratuity()
		first = gratuity.provision_release_journal_entry
		self.assertEqual(release_provision_for_gratuity(gratuity.name), first)

		# even if the link on the Gratuity were lost, the existing release is found, not re-posted
		frappe.db.set_value("Gratuity", gratuity.name, "provision_release_journal_entry", None)
		self.assertEqual(release_provision_for_gratuity(gratuity.name), first)
		self.assertEqual(
			frappe.db.get_value("Gratuity", gratuity.name, "provision_release_journal_entry"), first
		)
		self.assertEqual(self.release_entries(gratuity.name), [first])
		self.assertEqual(self.provision_balance(), 0)

	def test_repair_requires_submitted_gratuity(self):
		from gratuity_custom.events.gratuity import release_provision_for_gratuity

		gratuity = self.make_gratuity(submit=False)
		with self.assertRaisesRegex(frappe.ValidationError, "not submitted"):
			release_provision_for_gratuity(gratuity.name)

	def test_failed_release_leaves_nothing_behind(self):
		from unittest.mock import patch

		from gratuity_custom.events import gratuity as gratuity_events

		real_submit = gratuity_events.submit_gratuity_journal_entry

		def submit_then_fail(*args, **kwargs):
			real_submit(*args, **kwargs)  # the release JE is created and submitted...
			raise frappe.ValidationError("boom")  # ...then the Gratuity submit fails

		balance = self.provision_balance()
		gratuity = self.make_gratuity(submit=False)
		with patch.object(gratuity_events, "submit_gratuity_journal_entry", side_effect=submit_then_fail):
			with self.assertFailsAndRollsBack("boom"):
				gratuity.submit()

		self.assertEqual(frappe.db.get_value("Gratuity", gratuity.name, "docstatus"), 0)
		self.assertFalse(frappe.db.exists("Journal Entry", {"gratuity": gratuity.name}))
		self.assertFalse(
			frappe.db.exists("GL Entry", {"voucher_type": "Gratuity", "voucher_no": gratuity.name})
		)
		self.assertEqual(self.provision_balance(), balance)
