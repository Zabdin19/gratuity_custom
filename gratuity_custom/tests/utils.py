"""Self-contained fixtures for gratuity_custom tests.

Everything is created inside the test transaction: FrappeTestCase rolls back at class teardown and
GratuityTestCase additionally rolls back to a savepoint after every test. No site/business data is read
or modified, and no ERPNext/HRMS `_Test*` records are required.
"""

from contextlib import contextmanager

import frappe
from erpnext.accounts.utils import FiscalYearError, get_fiscal_year
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_months, get_first_day, get_last_day, getdate

TEST_COMPANY = "_Test Gratuity Company"
TEST_COMPANY_ABBR = "_TGC"
BASIC_COMPONENT = "_Test Gratuity Basic"
SALARY_STRUCTURE = "_Test Gratuity Structure"
GRATUITY_RULE = "_Test Gratuity Rule 21-30"


class GratuityTestCase(FrappeTestCase):
	"""Per-test savepoint on top of FrappeTestCase's class-level rollback."""

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.savepoint("gratuity_custom_test")

	def tearDown(self):
		frappe.db.rollback(save_point="gratuity_custom_test")
		frappe.set_user("Administrator")

	@contextmanager
	def assertFailsAndRollsBack(self, expected_regex):
		"""Like a failing web request: assert the error, then undo any partial writes before it."""
		frappe.db.savepoint("gratuity_custom_expected_failure")
		try:
			with self.assertRaisesRegex(frappe.ValidationError, expected_regex):
				yield
		finally:
			frappe.db.rollback(save_point="gratuity_custom_expected_failure")


def _default_currency():
	return frappe.db.get_default("currency") or "USD"


def make_company(name=TEST_COMPANY, abbr=TEST_COMPANY_ABBR):
	if frappe.db.exists("Company", name):
		return frappe.get_doc("Company", name)

	country = frappe.db.get_single_value("System Settings", "country") or frappe.db.get_default("country")
	company = frappe.get_doc(
		{
			"doctype": "Company",
			"company_name": name,
			"abbr": abbr,
			"default_currency": _default_currency(),
			"country": country,
			"chart_of_accounts": "Standard",
		}
	).insert()

	holiday_list = frappe.get_doc(
		{
			"doctype": "Holiday List",
			"holiday_list_name": f"{name} Holidays",
			"from_date": "2010-01-01",
			"to_date": "2035-12-31",
		}
	).insert()
	company.db_set("default_holiday_list", holiday_list.name)
	return company


def ensure_fiscal_years(company, from_year=2015, to_year=2030):
	"""Create company-scoped calendar fiscal years only where no fiscal year covers the dates."""
	for year in range(from_year, to_year + 1):
		try:
			get_fiscal_year(f"{year}-01-01", company=company)
			get_fiscal_year(f"{year}-12-31", company=company)
		except FiscalYearError:
			frappe.clear_messages()
			frappe.get_doc(
				{
					"doctype": "Fiscal Year",
					"year": f"{company} {year}",
					"year_start_date": f"{year}-01-01",
					"year_end_date": f"{year}-12-31",
					"companies": [{"company": company}],
				}
			).insert()


def _group(company, account_name):
	return frappe.db.get_value("Account", {"company": company, "account_name": account_name, "is_group": 1})


def make_account(company, account_name, parent_name, account_type=""):
	abbr = frappe.db.get_value("Company", company, "abbr")
	name = f"{account_name} - {abbr}"
	if frappe.db.exists("Account", name):
		return name
	return (
		frappe.get_doc(
			{
				"doctype": "Account",
				"account_name": account_name,
				"parent_account": _group(company, parent_name),
				"company": company,
				"account_type": account_type,
			}
		)
		.insert()
		.name
	)


def make_gratuity_accounts(company):
	return frappe._dict(
		expense=make_account(company, "Gratuity Expense", "Indirect Expenses"),
		reversal=make_account(company, "Gratuity Expense Reversal", "Indirect Expenses"),
		provision=make_account(company, "Gratuity Provision", "Current Liabilities"),
		payable=make_account(company, "Gratuity Payable", "Current Liabilities", "Payable"),
	)


def configure_company(company, accounts, gratuity_rule=None):
	doc = frappe.get_doc("Company", company)
	doc.gratuity_expense_account = accounts.expense
	doc.gratuity_expense_reversal_account = accounts.reversal
	doc.gratuity_provision_account = accounts.provision
	doc.default_gratuity_rule = gratuity_rule
	doc.save()
	return doc


def make_gratuity_rule(
	name=GRATUITY_RULE,
	method="Take Exact Completed Years",
	based_on="Sum of all previous slabs",
	slabs=None,
):
	"""The rule configuration approved for the UAT: 21 days (0.7) for years 0-5, 30 days (1.0) after."""
	if frappe.db.exists("Gratuity Rule", name):
		return name
	make_basic_component()
	slabs = slabs or [
		{"from_year": 0, "to_year": 5, "fraction_of_applicable_earnings": 21 / 30},
		{"from_year": 5, "to_year": 0, "fraction_of_applicable_earnings": 1},
	]
	rule = frappe.get_doc(
		{
			"doctype": "Gratuity Rule",
			"name": name,
			"calculate_gratuity_amount_based_on": based_on,
			"work_experience_calculation_function": method,
			"total_working_days_per_year": 365,
			"minimum_year_for_gratuity": 1,
			"applicable_earnings_component": [{"salary_component": BASIC_COMPONENT}],
			"gratuity_rule_slabs": slabs,
		}
	)
	rule.insert(set_name=name)
	return rule.name


def make_basic_component():
	if not frappe.db.exists("Salary Component", BASIC_COMPONENT):
		frappe.get_doc(
			{
				"doctype": "Salary Component",
				"salary_component": BASIC_COMPONENT,
				"salary_component_abbr": "_TGB",
				"type": "Earning",
				"depends_on_payment_days": 1,
			}
		).insert()
	return BASIC_COMPONENT


def make_salary_structure(company):
	if frappe.db.exists("Salary Structure", SALARY_STRUCTURE):
		return SALARY_STRUCTURE
	make_basic_component()
	structure = frappe.get_doc(
		{
			"doctype": "Salary Structure",
			"name": SALARY_STRUCTURE,
			"company": company,
			"currency": frappe.db.get_value("Company", company, "default_currency"),
			"payroll_frequency": "Monthly",
			"earnings": [
				{
					"salary_component": BASIC_COMPONENT,
					"abbr": "_TGB",
					"amount_based_on_formula": 1,
					"formula": "base",
				}
			],
		}
	)
	structure.insert(set_name=SALARY_STRUCTURE)
	structure.submit()
	return structure.name


def make_employee(company, date_of_joining, first_name="_Test Gratuity Employee", **kwargs):
	return (
		frappe.get_doc(
			{
				"doctype": "Employee",
				"first_name": first_name,
				"gender": "Male",
				"date_of_birth": "1985-01-01",
				"date_of_joining": date_of_joining,
				"company": company,
				"status": "Active",
				"holiday_list": frappe.db.get_value("Company", company, "default_holiday_list"),
				**kwargs,
			}
		)
		.insert()
		.name
	)


def assign_salary(employee, company, base, from_date):
	structure = make_salary_structure(company)
	assignment = frappe.get_doc(
		{
			"doctype": "Salary Structure Assignment",
			"employee": employee,
			"salary_structure": structure,
			"company": company,
			"from_date": from_date,
			"base": base,
			"currency": frappe.db.get_value("Company", company, "default_currency"),
		}
	)
	assignment.insert()
	assignment.submit()
	return assignment.name


def make_salary_slip(employee, month_date):
	"""Create and submit a real monthly Salary Slip through the standard HRMS mapper."""
	from hrms.payroll.doctype.salary_structure.salary_structure import make_salary_slip as map_slip

	start, end = get_first_day(month_date), get_last_day(month_date)
	slip = map_slip(SALARY_STRUCTURE, employee=employee, posting_date=end)
	slip.start_date, slip.end_date, slip.posting_date = start, end, end
	slip.insert()
	slip.submit()
	return slip


def make_employee_with_salary(company, date_of_joining, base, slip_months, **kwargs):
	"""Employee + salary assignment from joining + submitted slips for each month in `slip_months`."""
	employee = make_employee(company, date_of_joining, **kwargs)
	assign_salary(employee, company, base, date_of_joining)
	for month in slip_months:
		make_salary_slip(employee, month)
	return employee


def setup_gratuity_company():
	"""Company, fiscal years, accounts, approved Gratuity Rule and company configuration."""
	company = make_company().name
	ensure_fiscal_years(company)
	accounts = make_gratuity_accounts(company)
	rule = make_gratuity_rule()
	configure_company(company, accounts, rule)
	return frappe._dict(company=company, accounts=accounts, rule=rule)


def month_end(date_str, months=0):
	return get_last_day(add_months(getdate(date_str), months))


def make_gratuity(company, employee, rule, payable_account, posting_date, submit=True, **kwargs):
	"""Standard HRMS Gratuity settled through the Full and Final Statement (not via salary slip)."""
	doc = frappe.get_doc(
		{
			"doctype": "Gratuity",
			"employee": employee,
			"company": company,
			"posting_date": posting_date,
			"gratuity_rule": rule,
			"pay_via_salary_slip": 0,
			"expense_account": frappe.db.get_value("Company", company, "gratuity_expense_account"),
			"payable_account": payable_account,
			"cost_center": frappe.db.get_value("Company", company, "cost_center"),
			**kwargs,
		}
	).insert()
	if submit:
		doc.submit()
	return doc


def settle_through_full_and_final(gratuity, payment_date):
	"""Standard Full and Final Statement for the Gratuity, then its standard payment Journal Entry."""
	from hrms.hr.doctype.full_and_final_statement.full_and_final_statement import get_account_and_amount

	fnf = frappe.get_doc(
		{
			"doctype": "Full and Final Statement",
			"employee": gratuity.employee,
			"transaction_date": payment_date,
		}
	).insert()
	row = next(r for r in fnf.payables if r.component == "Gratuity")
	row.reference_document = gratuity.name
	row.account, row.amount = get_account_and_amount("Gratuity", gratuity.name, gratuity.company)
	row.status = "Settled"
	# remove the unused placeholder rows HRMS pre-fills (Expense Claim, Bonus, Advance, ...)
	fnf.set("payables", [row])
	fnf.set("receivables", [])
	fnf.save()
	fnf.submit()

	jv = fnf.create_journal_entry()
	jv.posting_date = payment_date
	jv.cheque_no, jv.cheque_date = f"FNF-{fnf.name}", payment_date
	jv.accounts[-1].account = frappe.db.get_value(
		"Account", {"company": gratuity.company, "account_type": "Cash", "is_group": 0}
	)
	jv.insert()
	jv.submit()
	return fnf, jv


def make_accrual(company, from_date, to_date=None, posting_date=None, employees=None, submit=False, **kwargs):
	"""Gratuity Accrual for a period. `employees=None` uses the standard Get Employees selection."""
	to_date = to_date or get_last_day(from_date)
	accrual = frappe.get_doc(
		{
			"doctype": "Gratuity Accrual",
			"company": company,
			"from_date": from_date,
			"to_date": to_date,
			"posting_date": posting_date or to_date,
			**kwargs,
		}
	)
	if employees is None:
		accrual.set_missing_values()
		accrual.get_employees()
	else:
		for employee in employees:
			accrual.append("employees", {"employee": employee})
	accrual.insert()
	if submit:
		accrual.submit()
	return accrual
