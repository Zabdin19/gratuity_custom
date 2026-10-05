import frappe

from gratuity_custom.accounts import is_provisioning_enabled
from gratuity_custom.tests.utils import (
	GratuityTestCase,
	configure_company,
	make_account,
	make_company,
	make_gratuity_accounts,
	make_gratuity_rule,
)


class TestGratuitySettings(GratuityTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = make_company().name
		cls.accounts = make_gratuity_accounts(cls.company)

	def test_custom_fields_installed(self):
		for doctype, fieldnames in {
			"Company": [
				"gratuity_expense_account",
				"gratuity_expense_reversal_account",
				"gratuity_provision_account",
				"default_gratuity_rule",
			],
			"Journal Entry": ["gratuity_entry_type", "gratuity_accrual", "gratuity"],
			"Gratuity": ["provision_release_journal_entry"],
		}.items():
			meta = frappe.get_meta(doctype)
			for fieldname in fieldnames:
				self.assertTrue(meta.has_field(fieldname), f"{doctype}.{fieldname} missing")

	def test_valid_configuration(self):
		self.assertFalse(is_provisioning_enabled(self.company))
		configure_company(self.company, self.accounts)
		self.assertTrue(is_provisioning_enabled(self.company))

	def test_partial_configuration_rejected(self):
		accounts = frappe._dict(self.accounts, reversal=None)
		self.assertRaises(frappe.ValidationError, configure_company, self.company, accounts)

	def test_payable_provision_account_rejected(self):
		accounts = frappe._dict(self.accounts, provision=self.accounts.payable)
		with self.assertRaisesRegex(frappe.ValidationError, "blank Account Type"):
			configure_company(self.company, accounts)

	def test_typed_provision_account_rejected(self):
		typed = make_account(self.company, "Gratuity Provision Typed", "Current Liabilities", "Temporary")
		accounts = frappe._dict(self.accounts, provision=typed)
		self.assertRaises(frappe.ValidationError, configure_company, self.company, accounts)

	def test_non_liability_provision_account_rejected(self):
		accounts = frappe._dict(self.accounts, provision=self.accounts.expense)
		with self.assertRaisesRegex(frappe.ValidationError, "Liability"):
			configure_company(self.company, accounts)

	def test_group_provision_account_rejected(self):
		group = frappe.db.get_value(
			"Account", {"company": self.company, "account_name": "Current Liabilities", "is_group": 1}
		)
		accounts = frappe._dict(self.accounts, provision=group)
		with self.assertRaisesRegex(frappe.ValidationError, "group"):
			configure_company(self.company, accounts)

	def test_expense_account_must_be_expense(self):
		accounts = frappe._dict(self.accounts, expense=self.accounts.provision)
		with self.assertRaisesRegex(frappe.ValidationError, "Expense account"):
			configure_company(self.company, accounts)

	def test_reversal_account_must_be_profit_and_loss(self):
		accounts = frappe._dict(self.accounts, reversal=self.accounts.provision)
		with self.assertRaisesRegex(frappe.ValidationError, "Profit and Loss"):
			configure_company(self.company, accounts)

	def test_account_of_other_company_rejected(self):
		other = make_company("_Test Gratuity Company 2", "_TGC2").name
		other_provision = make_account(other, "Gratuity Provision", "Current Liabilities")
		accounts = frappe._dict(self.accounts, provision=other_provision)
		with self.assertRaisesRegex(frappe.ValidationError, "does not belong"):
			configure_company(self.company, accounts)

	def test_income_account_rejected_as_expense(self):
		income = make_account(self.company, "Gratuity Income", "Indirect Income")
		with self.assertRaisesRegex(frappe.ValidationError, "Expense account"):
			configure_company(self.company, frappe._dict(self.accounts, expense=income))
		# an Income account is still Profit and Loss, so it is acceptable for the reversal side
		configure_company(self.company, frappe._dict(self.accounts, reversal=income))

	def test_foreign_currency_account_rejected(self):
		company_currency = frappe.db.get_value("Company", self.company, "default_currency")
		other_currency = "USD" if company_currency != "USD" else "EUR"
		foreign = frappe.get_doc(
			{
				"doctype": "Account",
				"account_name": "Gratuity Provision Foreign",
				"parent_account": frappe.db.get_value(
					"Account", {"company": self.company, "account_name": "Current Liabilities", "is_group": 1}
				),
				"company": self.company,
				"account_currency": other_currency,
			}
		).insert()
		with self.assertRaisesRegex(frappe.ValidationError, "company currency"):
			configure_company(self.company, frappe._dict(self.accounts, provision=foreign.name))

	def test_rounding_default_rule_rejected(self):
		rounding = make_gratuity_rule("_Test Gratuity Rule Round", method="Round off Work Experience")
		with self.assertRaisesRegex(frappe.ValidationError, "Take Exact Completed Years"):
			configure_company(self.company, self.accounts, rounding)
		configure_company(self.company, self.accounts, make_gratuity_rule())

	def test_same_account_for_expense_and_reversal_allowed(self):
		accounts = frappe._dict(self.accounts, reversal=self.accounts.expense)
		configure_company(self.company, accounts)
		self.assertEqual(
			frappe.db.get_value("Company", self.company, "gratuity_expense_reversal_account"),
			self.accounts.expense,
		)
