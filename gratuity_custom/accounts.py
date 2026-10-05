"""Company-level gratuity account configuration and validation."""

import frappe
from frappe import _, bold
from frappe.query_builder.functions import Sum
from frappe.utils import flt

GRATUITY_ACCOUNT_FIELDS = (
	"gratuity_expense_account",
	"gratuity_expense_reversal_account",
	"gratuity_provision_account",
)


def get_gratuity_settings(company: str) -> frappe._dict:
	"""Gratuity accounts and default rule configured on the Company."""
	values = frappe.db.get_value(
		"Company",
		company,
		[*GRATUITY_ACCOUNT_FIELDS, "default_gratuity_rule", "cost_center"],
		as_dict=True,
	)
	return values or frappe._dict()


def get_provision_balances(company: str, provision_account: str, employees: list, as_of_date) -> dict:
	"""Employee-wise credit balance of the provision account from the standard General Ledger.

	The GL is the single source of truth for the accrued provision: it already reflects monthly
	provisions, reversals and final-settlement releases. Returns {employee: credit - debit}.
	"""
	if not employees:
		return {}

	gle = frappe.qb.DocType("GL Entry")
	rows = (
		frappe.qb.from_(gle)
		.select(gle.party, Sum(gle.credit - gle.debit).as_("balance"))
		.where(
			(gle.company == company)
			& (gle.account == provision_account)
			& (gle.party_type == "Employee")
			& (gle.party.isin(list(employees)))
			& (gle.is_cancelled == 0)
			& (gle.posting_date <= as_of_date)
		)
		.groupby(gle.party)
	).run(as_dict=True)
	precision = frappe.get_precision("GL Entry", "credit")
	return {row.party: flt(row.balance, precision) for row in rows}


def get_employee_provision_accounts(company: str, employee: str) -> list:
	"""Provision accounts that may carry the employee's accrued gratuity, each with the Expense Reversal
	account to release it to.

	Sources: the Company configuration, plus the accounts on every submitted Gratuity Accrual for the
	employee (accruals may be posted with accounts entered on the document). Without the second source a
	provision posted while the Company was not (or differently) configured would never be released.
	The amount to release is always read from the General Ledger, not from these documents.
	"""
	pairs = {}
	settings = get_gratuity_settings(company)
	if settings.gratuity_provision_account:
		pairs[settings.gratuity_provision_account] = settings.gratuity_expense_reversal_account

	accrual = frappe.qb.DocType("Gratuity Accrual")
	row = frappe.qb.DocType("Gratuity Accrual Employee")
	accrued = (
		frappe.qb.from_(accrual)
		.join(row)
		.on(row.parent == accrual.name)
		.select(accrual.provision_account, accrual.expense_reversal_account)
		.where((accrual.docstatus == 1) & (accrual.company == company) & (row.employee == employee))
		.orderby(accrual.posting_date, order=frappe.qb.desc)
		.orderby(accrual.creation, order=frappe.qb.desc)
	).run(as_dict=True)
	for d in accrued:
		# the most recent accrual's reversal account wins for an account not configured on the Company
		pairs.setdefault(d.provision_account, d.expense_reversal_account)

	return [frappe._dict(provision_account=k, expense_reversal_account=v) for k, v in pairs.items()]


def validate_no_provision_entries_after(company: str, provision_account: str, employees: list, posting_date):
	"""Provision balances are read as of a posting date, so no provision entry may be dated after it."""
	if not employees:
		return
	gle = frappe.qb.DocType("GL Entry")
	latest = (
		frappe.qb.from_(gle)
		.select(gle.party, gle.posting_date)
		.where(
			(gle.company == company)
			& (gle.account == provision_account)
			& (gle.party_type == "Employee")
			& (gle.party.isin(list(employees)))
			& (gle.is_cancelled == 0)
			& (gle.posting_date > posting_date)
		)
		.orderby(gle.posting_date, order=frappe.qb.desc)
		.limit(1)
	).run(as_dict=True)
	if latest:
		frappe.throw(
			_(
				"Employee {0} has gratuity provision entries dated {1}, after this Posting Date. Use a Posting Date on or after {1}."
			).format(bold(latest[0].party), latest[0].posting_date)
		)


def get_employees_with_provision(company: str, provision_account: str, as_of_date) -> list:
	"""Employees carrying a non-zero provision balance on the given date."""
	gle = frappe.qb.DocType("GL Entry")
	rows = (
		frappe.qb.from_(gle)
		.select(gle.party)
		.where(
			(gle.company == company)
			& (gle.account == provision_account)
			& (gle.party_type == "Employee")
			& (gle.is_cancelled == 0)
			& (gle.posting_date <= as_of_date)
		)
		.groupby(gle.party)
		.having(Sum(gle.credit - gle.debit) != 0)
	).run(pluck=True)
	return rows


def is_provisioning_enabled(company: str) -> bool:
	"""Provision accounting is active for a company once its provision account is configured."""
	return bool(company and frappe.db.get_value("Company", company, "gratuity_provision_account"))


def validate_gratuity_accounts(
	company: str,
	expense_account: str | None,
	expense_reversal_account: str | None,
	provision_account: str | None,
	mandatory: bool = False,
):
	"""Validate the three gratuity accounts for a company.

	The accounts are configured as a set: if any one is given (or `mandatory` is set) all are required.
	"""
	accounts = {
		"gratuity_expense_account": expense_account,
		"gratuity_expense_reversal_account": expense_reversal_account,
		"gratuity_provision_account": provision_account,
	}
	if not (mandatory or any(accounts.values())):
		return

	labels = {
		"gratuity_expense_account": _("Gratuity Expense Account"),
		"gratuity_expense_reversal_account": _("Gratuity Expense Reversal Account"),
		"gratuity_provision_account": _("Gratuity Provision Account"),
	}
	missing = [labels[key] for key, value in accounts.items() if not value]
	if missing:
		frappe.throw(
			_("Please set {0} for company {1}").format(", ".join(bold(m) for m in missing), bold(company)),
			title=_("Gratuity Accounts Missing"),
		)

	company_currency = frappe.get_cached_value("Company", company, "default_currency")
	for key, account in accounts.items():
		details = frappe.db.get_value(
			"Account",
			account,
			[
				"company",
				"is_group",
				"disabled",
				"root_type",
				"report_type",
				"account_type",
				"account_currency",
			],
			as_dict=True,
		)
		label = labels[key]
		if not details:
			frappe.throw(_("{0}: Account {1} does not exist").format(label, bold(account)))
		if details.company != company:
			frappe.throw(
				_("{0}: Account {1} does not belong to company {2}").format(
					label, bold(account), bold(company)
				)
			)
		if details.is_group:
			frappe.throw(_("{0}: Account {1} is a group account").format(label, bold(account)))
		if details.disabled:
			frappe.throw(_("{0}: Account {1} is disabled").format(label, bold(account)))
		if details.account_currency and details.account_currency != company_currency:
			# Provision entries are posted in company currency (no multi-currency Journal Entries).
			frappe.throw(
				_("{0}: Account {1} must be in company currency {2}, not {3}").format(
					label, bold(account), bold(company_currency), bold(details.account_currency)
				)
			)

		if key == "gratuity_expense_account" and details.root_type != "Expense":
			frappe.throw(_("{0}: Account {1} must be an Expense account").format(label, bold(account)))

		if key == "gratuity_expense_reversal_account" and details.report_type != "Profit and Loss":
			frappe.throw(_("{0}: Account {1} must be a Profit and Loss account").format(label, bold(account)))

		if key == "gratuity_provision_account":
			if details.root_type != "Liability":
				frappe.throw(_("{0}: Account {1} must be a Liability account").format(label, bold(account)))
			if details.account_type:
				# A Payable (or any typed) account would push provisions into the payment ledger and
				# AP reports, and ERPNext only permits a party on untyped or Receivable/Payable accounts.
				frappe.throw(
					_(
						"{0}: Account {1} must have a blank Account Type (current Account Type: {2}). "
						"Provisions are not payables."
					).format(label, bold(account), bold(details.account_type))
				)
