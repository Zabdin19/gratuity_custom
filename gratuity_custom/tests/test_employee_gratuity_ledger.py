import frappe

from gratuity_custom.gratuity_custom.report.employee_gratuity_ledger.employee_gratuity_ledger import execute
from gratuity_custom.tests.utils import (
	GratuityTestCase,
	make_accrual,
	make_employee_with_salary,
	make_gratuity,
	settle_through_full_and_final,
	setup_gratuity_company,
)


class TestEmployeeGratuityLedger(GratuityTestCase):
	"""Jan + Feb provisions, Feb reversed and recalculated, employee A resigns mid-March and is settled."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.setup = setup_gratuity_company()
		cls.company, cls.accounts = cls.setup.company, cls.setup.accounts
		cls.leaver = make_employee_with_salary(
			cls.company, "2019-02-01", 3000, ["2024-01-15"], first_name="_TG Leaver"
		)
		cls.stayer = make_employee_with_salary(
			cls.company, "2020-06-01", 2000, ["2024-01-15"], first_name="_TG Stayer"
		)
		employees = [cls.leaver, cls.stayer]

		make_accrual(cls.company, "2024-01-01", employees=employees, submit=True)
		feb = make_accrual(cls.company, "2024-02-01", employees=employees, submit=True)
		feb.reverse_provision("2024-03-05")
		feb.reload()
		cls.recalculation = frappe.get_doc("Gratuity Accrual", feb.create_recalculation())
		cls.recalculation.submit()
		cls.feb = feb

		frappe.db.set_value("Employee", cls.leaver, {"status": "Left", "relieving_date": "2024-03-14"})
		cls.gratuity = make_gratuity(
			cls.company, cls.leaver, cls.setup.rule, cls.accounts.payable, "2024-03-14"
		)
		cls.fnf, cls.payment = settle_through_full_and_final(cls.gratuity, "2024-03-20")

	def run_report(self, **filters):
		columns, data = execute({"company": self.company, **filters})
		self.assertTrue(columns)
		return data

	def test_without_optional_filters(self):
		data = self.run_report()
		self.assertEqual({row.employee for row in data}, {self.leaver, self.stayer})

		leaver_types = [row.transaction_type for row in data if row.employee == self.leaver]
		self.assertEqual(
			leaver_types,
			[
				"Provision",  # Jan
				"Opening Reversal",  # Feb: reverse Jan balance
				"Provision",  # Feb
				"Provision Reversal",  # manual reversal of Feb
				"Recalculation",  # Feb recalculated
				"Gratuity",  # standard Gratuity GL is posted first on submit...
				"Provision Release",  # ...then the provision release hook
				"Final Settlement",  # Full and Final Statement payment
			],
		)
		stayer_types = {row.transaction_type for row in data if row.employee == self.stayer}
		self.assertEqual(
			stayer_types, {"Provision", "Opening Reversal", "Provision Reversal", "Recalculation"}
		)

	def test_balances_close_after_settlement(self):
		data = self.run_report(employee=self.leaver)
		last = {}
		for row in data:
			last[row.account] = row.balance
		self.assertEqual(last[self.accounts.provision], 0)
		self.assertEqual(last[self.accounts.payable], 0)

	def test_employee_filter(self):
		data = self.run_report(employee=self.stayer)
		self.assertTrue(data)
		self.assertEqual({row.employee for row in data}, {self.stayer})

	def test_transaction_type_filter(self):
		provisions = self.run_report(transaction_type="Provision")
		self.assertEqual(len(provisions), 4)  # Jan + Feb for two employees
		self.assertTrue(
			all(row.transaction_type == "Provision" and row.gratuity_accrual for row in provisions)
		)

		settlement = self.run_report(transaction_type="Final Settlement")
		self.assertEqual(len(settlement), 1)
		row = settlement[0]
		self.assertEqual(
			(
				row.employee,
				row.account,
				row.debit,
				row.gratuity,
				row.full_and_final_statement,
				row.voucher_no,
			),
			(
				self.leaver,
				self.accounts.payable,
				self.gratuity.amount,
				self.gratuity.name,
				self.fnf.name,
				self.payment.name,
			),
		)

		release = self.run_report(transaction_type="Provision Release")
		self.assertEqual(
			[(r.gratuity, r.voucher_no) for r in release],
			[(self.gratuity.name, self.gratuity.provision_release_journal_entry)],
		)

		recalculation = self.run_report(transaction_type="Recalculation")
		self.assertTrue(all(r.gratuity_accrual == self.recalculation.name for r in recalculation))

	def test_date_filters_with_opening_balance(self):
		data = self.run_report(employee=self.leaver, from_date="2024-03-01", to_date="2024-03-31")
		opening = data[0]
		self.assertEqual((opening.transaction_type, opening.account), ("Opening", self.accounts.provision))
		feb_liability = next(r.liability for r in self.feb.employees if r.employee == self.leaver)
		self.assertEqual(opening.balance, feb_liability)

		period = data[1:]
		self.assertTrue(all(str(row.posting_date) >= "2024-03-01" for row in period))
		self.assertEqual(
			[row.transaction_type for row in period],
			["Provision Reversal", "Recalculation", "Gratuity", "Provision Release", "Final Settlement"],
		)

		self.assertFalse(self.run_report(employee=self.leaver, to_date="2023-12-31"))

	def test_invalid_date_range(self):
		with self.assertRaisesRegex(frappe.ValidationError, "From Date cannot be after"):
			self.run_report(from_date="2024-03-31", to_date="2024-03-01")
