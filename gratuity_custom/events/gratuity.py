"""Final settlement: release the accrued provision when the standard HRMS Gratuity is submitted.

Standard Gratuity keeps its own accounting (Dr Gratuity Expense / Cr Gratuity Payable, party Employee)
for the amount HRMS calculates up to the relieving date. This hook only adds the release of the
employee's accrued provision (Dr Gratuity Provision / Cr Gratuity Expense Reversal), so that:

    expense   = accrued provisions + Gratuity amount - released provision = Gratuity amount
    provision = 0
    payable   = Gratuity amount (paid through the standard Full and Final Statement / Payment Entry)

Provisioning applies to a Gratuity when its Company has a Gratuity Provision Account configured or the
employee has submitted Gratuity Accruals. Otherwise the Gratuity is left completely standard.
"""

import frappe
from frappe import _, bold

from gratuity_custom.accounts import (
	get_employee_provision_accounts,
	get_gratuity_settings,
	get_provision_balances,
	validate_no_provision_entries_after,
)
from gratuity_custom.calculation import validate_gratuity_rule
from gratuity_custom.journal_entries import (
	RELEASE,
	build_provision_lines,
	cancel_gratuity_journal_entry,
	submit_gratuity_journal_entry,
)


def validate(doc, method=None):
	if not get_employee_provision_accounts(doc.company, doc.employee):
		return
	settings = get_gratuity_settings(doc.company)

	if doc.pay_via_salary_slip:
		frappe.throw(
			_(
				"Gratuity for employee {0} is accrued through provisions. Uncheck {1} so the gratuity is settled through the Full and Final Statement."
			).format(bold(doc.employee), bold(_("Pay via Salary Slip"))),
			title=_("Provision-based Final Settlement"),
		)

	# Validation only: this hook never changes the standard Gratuity's fields.
	# Accrual, final Gratuity and provision release must all use the same (Exact) calculation rule.
	validate_gratuity_rule(doc.gratuity_rule)
	if settings.default_gratuity_rule and doc.gratuity_rule != settings.default_gratuity_rule:
		frappe.throw(
			_(
				"Gratuity Rule {0} differs from company {1}'s Default Gratuity Rule {2}, which is used for its gratuity provisions. Please select {2}."
			).format(bold(doc.gratuity_rule), bold(doc.company), bold(settings.default_gratuity_rule)),
			title=_("Provision-based Final Settlement"),
		)

	if not doc.expense_account:
		frappe.throw(
			_("Please set {0} (usually the Gratuity Expense Account)").format(bold(_("Expense Account"))),
			title=_("Provision-based Final Settlement"),
		)
	if frappe.db.get_value("Account", doc.expense_account, "root_type") != "Expense":
		frappe.throw(
			_("Expense Account {0} must be an Expense account; the provision is released separately").format(
				bold(doc.expense_account)
			)
		)


def on_submit(doc, method=None):
	release_provision(doc)


def release_provision(doc):
	"""Post one Provision Release JE for the employee's provision balance. Idempotent."""
	existing = doc.get("provision_release_journal_entry") or frappe.db.get_value(
		"Journal Entry",
		{"gratuity": doc.name, "gratuity_entry_type": "Provision Release", "docstatus": 1},
		"name",
	)
	if existing:
		if not doc.get("provision_release_journal_entry"):
			doc.db_set("provision_release_journal_entry", existing)
		return existing

	cost_center = (
		doc.cost_center
		or frappe.db.get_value("Employee", doc.employee, "payroll_cost_center")
		or get_gratuity_settings(doc.company).cost_center
	)
	lines = []
	for accounts in get_employee_provision_accounts(doc.company, doc.employee):
		validate_no_provision_entries_after(
			doc.company, accounts.provision_account, [doc.employee], doc.posting_date
		)
		balance = get_provision_balances(
			doc.company, accounts.provision_account, [doc.employee], doc.posting_date
		).get(doc.employee, 0)
		if balance:
			lines += build_provision_lines(
				accounts.provision_account,
				accounts.expense_reversal_account,
				[(doc.employee, cost_center, balance)],
				RELEASE,
			)
	if not lines:
		return None

	name = submit_gratuity_journal_entry(
		doc.company,
		doc.posting_date,
		"Provision Release",
		lines,
		_("Release of accrued gratuity provision on final settlement through Gratuity {0}").format(doc.name),
		gratuity=doc.name,
	)
	doc.db_set("provision_release_journal_entry", name)
	return name


def release_provision_for_gratuity(gratuity: str):
	"""Repair entry point (bench execute) for a submitted Gratuity whose release was not posted."""
	doc = frappe.get_doc("Gratuity", gratuity)
	if doc.docstatus != 1:
		frappe.throw(_("Gratuity {0} is not submitted").format(bold(gratuity)))
	return release_provision(doc)


def on_cancel(doc, method=None):
	# Runs after the Gratuity is marked cancelled, so the release entry's back-link check passes.
	if doc.get("provision_release_journal_entry"):
		cancel_gratuity_journal_entry(doc.provision_release_journal_entry)
