"""Keep Gratuity rows of the standard Full and Final Statement in step with the linked Gratuity.

Standard HRMS adds an empty "Gratuity" payable placeholder and only fetches amount/account in the
browser when a reference is picked; it never reads the Gratuity's payment status. This module, on the
server:

* fills the Gratuity row(s) of a new statement with the employee's submitted Gratuity documents;
* syncs Amount and Account from the linked submitted Gratuity on every save;
* marks the row Settled when the Gratuity is paid. An unpaid Gratuity starts Unsettled and returns to
  Unsettled when its payment is cancelled, but a user may still mark it Settled: in the standard flow
  the statement is submitted first and its own Journal Entry then pays the Gratuity;
* rejects the same Gratuity on two rows;
* blocks a statement's Journal Entry that would pay an already-paid Gratuity again (the row keeps the
  full Gratuity amount for audit, and the standard Journal Entry pays every row with an amount).

Only rows whose Reference Document Type is Gratuity are touched; Gratuities paid via Salary Slip are
left to the standard behaviour.
"""

import frappe
from frappe import _, bold
from frappe.utils import flt

FNF = "Full and Final Statement"
FNF_ROW = "Full and Final Outstanding Statement"


def validate(doc, method=None):
	if doc.is_new():
		fill_gratuity_rows(doc)

	seen = set()
	for row in gratuity_rows(doc):
		if not row.reference_document:
			continue
		if row.reference_document in seen:
			frappe.throw(
				_("Row {0}: Gratuity {1} is already included in this statement").format(
					row.idx, bold(row.reference_document)
				)
			)
		seen.add(row.reference_document)
		sync_row(row)

	doc.set_totals()  # amounts may have changed after the standard totals were computed


def gratuity_rows(doc):
	return [row for row in doc.get("payables", []) if row.reference_document_type == "Gratuity"]


def fill_gratuity_rows(doc):
	"""Put each settleable Gratuity of the employee on a row, reusing the empty placeholder first."""
	rows = gratuity_rows(doc)
	referenced = {row.reference_document for row in rows if row.reference_document}
	placeholders = [row for row in rows if not row.reference_document]

	for gratuity in get_settlement_gratuities(doc.employee, doc.company, exclude_statement=doc.name):
		if gratuity in referenced:
			continue
		row = (
			placeholders.pop(0)
			if placeholders
			else doc.append(
				"payables",
				{"component": "Gratuity", "reference_document_type": "Gratuity", "status": "Unsettled"},
			)
		)
		row.reference_document = gratuity
		referenced.add(gratuity)


def sync_row(row):
	gratuity = get_gratuity(row.reference_document)
	if not gratuity:
		return
	row.amount = gratuity.amount
	row.account = gratuity.payable_account
	if is_paid(gratuity):
		row.status = "Settled"


def get_gratuity(name):
	"""Submitted Gratuity settled outside payroll, or None (left to standard behaviour)."""
	gratuity = frappe.db.get_value(
		"Gratuity",
		name,
		["docstatus", "amount", "paid_amount", "status", "payable_account", "pay_via_salary_slip"],
		as_dict=True,
	)
	if gratuity and gratuity.docstatus == 1 and not gratuity.pay_via_salary_slip:
		return gratuity
	return None


def is_paid(gratuity) -> bool:
	return gratuity.status == "Paid"


@frappe.whitelist()
def get_settlement_gratuities(employee, company, exclude_statement=None):
	"""Submitted Gratuities (not paid via salary slip) of the employee that no other non-cancelled
	Full and Final Statement already references."""
	if not (employee and company):
		return []
	gratuities = frappe.get_list(
		"Gratuity",
		filters={"employee": employee, "company": company, "docstatus": 1, "pay_via_salary_slip": 0},
		order_by="posting_date asc, creation asc",
		pluck="name",
	)
	if not gratuities:
		return []

	row, fnf = frappe.qb.DocType(FNF_ROW), frappe.qb.DocType(FNF)
	query = (
		frappe.qb.from_(row)
		.join(fnf)
		.on(row.parent == fnf.name)
		.select(row.reference_document)
		.where(
			(row.parenttype == FNF)
			& (row.reference_document_type == "Gratuity")
			& (row.reference_document.isin(gratuities))
			& (fnf.docstatus < 2)
		)
	)
	if exclude_statement:
		query = query.where(fnf.name != exclude_statement)
	taken = set(query.run(pluck=True))
	return [g for g in gratuities if g not in taken]


def refresh_statements_for_payment(doc, method=None):
	"""Payment Entry submit/cancel: update draft statements whose Gratuity payment status changed."""
	gratuities = {
		ref.reference_name for ref in doc.get("references", []) if ref.reference_doctype == "Gratuity"
	}
	for name in gratuities:
		gratuity = get_gratuity(name)
		if not gratuity:
			continue
		status = "Settled" if is_paid(gratuity) else "Unsettled"

		row, fnf = frappe.qb.DocType(FNF_ROW), frappe.qb.DocType(FNF)
		rows = (
			frappe.qb.from_(row)
			.join(fnf)
			.on(row.parent == fnf.name)
			.select(row.name, row.parent)
			.where(
				(row.parenttype == FNF)
				& (row.parentfield == "payables")
				& (row.reference_document_type == "Gratuity")
				& (row.reference_document == name)
				& (row.status != status)
				& (fnf.docstatus == 0)
			)
		).run(as_dict=True)
		for r in rows:
			frappe.db.set_value(FNF_ROW, r.name, "status", status)
			frappe.get_doc(FNF, r.parent).notify_update()


def validate_settlement_journal_entry(doc, method=None):
	"""Journal Entry validate: never pay an already-paid Gratuity again through a statement.

	The standard statement Journal Entry debits every payable row with an amount, whatever its
	Status, and the Gratuity row keeps the full amount. The authority is the Gratuity itself: per
	payable account, the entry may pay the employee at most what the statement still owes there,
	counting each Gratuity row at its outstanding amount (Gratuity amount - paid amount). An unpaid
	Gratuity is fully outstanding, so the standard flow is unchanged; other accounts are not checked.
	"""
	statements = {
		row.reference_name
		for row in doc.get("accounts", [])
		if row.reference_type == FNF and row.reference_name
	}
	for statement in statements:
		_validate_against_statement(doc, statement)


def _validate_against_statement(doc, statement):
	employee = frappe.db.get_value(FNF, statement, "employee")
	payables = frappe.get_all(
		FNF_ROW,
		filters={"parenttype": FNF, "parent": statement, "parentfield": "payables"},
		fields=["account", "amount", "paid_via_salary_slip", "reference_document_type", "reference_document"],
	)

	owed, paid_gratuities = {}, {}
	for row in payables:
		if not row.account or row.paid_via_salary_slip:
			continue
		amount = flt(row.amount)
		if row.reference_document_type == "Gratuity" and row.reference_document:
			gratuity = get_gratuity(row.reference_document)
			if gratuity and flt(gratuity.paid_amount) > 0:
				amount = max(flt(gratuity.amount) - flt(gratuity.paid_amount), 0)
				paid_gratuities.setdefault(row.account, []).append((row.reference_document, gratuity))
		owed[row.account] = owed.get(row.account, 0) + amount

	precision = frappe.get_precision("Journal Entry Account", "debit_in_account_currency")
	for account, gratuities in paid_gratuities.items():
		paying = sum(
			flt(r.debit_in_account_currency) - flt(r.credit_in_account_currency)
			for r in doc.get("accounts", [])
			if r.account == account and r.party_type == "Employee" and r.party == employee
		)
		excess = flt(paying - owed.get(account, 0), precision)
		if excess > 0:
			details = ", ".join(
				_("{0} (amount {1}, already paid {2})").format(
					bold(name), frappe.format(g.amount, "Currency"), frappe.format(g.paid_amount, "Currency")
				)
				for name, g in gratuities
			)
			frappe.throw(
				_(
					"This Journal Entry would pay {0} more to employee {1} on account {2} than Full and Final Statement {3} still owes, because Gratuity {4} is already paid. Remove that Gratuity line and reduce the payment line by the same amount."
				).format(
					bold(frappe.format(excess, "Currency")),
					bold(employee),
					bold(account),
					bold(statement),
					details,
				),
				title=_("Gratuity Already Paid"),
			)
