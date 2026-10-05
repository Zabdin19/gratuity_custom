# Copyright (c) 2026, zainulabdin and contributors
# For license information, please see license.txt

"""Employee-wise view of gratuity transactions, read from the standard General Ledger.

No separate ledger is kept: every row is a GL Entry with the Employee as party, classified by its source:

* Journal Entries posted by gratuity_custom (`gratuity_entry_type`): Opening Reversal, Provision,
  Recalculation (provision of an accrual that recalculates a reversed one), Provision Reversal,
  Provision Release (final settlement)
* standard Gratuity documents: Gratuity (payable)
* Payment Entries against a Gratuity: Gratuity Payment
* Full and Final Statement payment Journal Entries, for their Gratuity payable line: Final Settlement
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, getdate

TRANSACTION_TYPES = (
	"Opening Reversal",
	"Provision",
	"Recalculation",
	"Provision Reversal",
	"Provision Release",
	"Gratuity",
	"Gratuity Payment",
	"Final Settlement",
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	validate_filters(filters)
	return get_columns(), get_data(filters)


def validate_filters(filters):
	if not filters.company:
		frappe.throw(_("Company is required"))
	if filters.from_date and filters.to_date and getdate(filters.from_date) > getdate(filters.to_date):
		frappe.throw(_("From Date cannot be after To Date"))
	if filters.transaction_type and filters.transaction_type not in TRANSACTION_TYPES:
		frappe.throw(_("Invalid Transaction Type {0}").format(filters.transaction_type))


def get_data(filters):
	entries = get_gl_entries(filters)
	employee_names = dict(
		frappe.get_all(
			"Employee",
			filters={"name": ("in", list({e.employee for e in entries}))},
			fields=["name", "employee_name"],
			as_list=True,
		)
	)
	from_date = getdate(filters.from_date) if filters.from_date else None
	precision = frappe.get_precision("GL Entry", "debit")

	data, balances, opening = [], defaultdict(float), defaultdict(float)
	for entry in entries:
		key = (entry.employee, entry.account)
		balances[key] = flt(balances[key] + entry.credit - entry.debit, precision)

		if from_date and getdate(entry.posting_date) < from_date:
			opening[key] = balances[key]
			continue
		if filters.transaction_type and entry.transaction_type != filters.transaction_type:
			continue

		if key in opening:
			data.append(opening_row(key, opening.pop(key), from_date, employee_names))
		entry.employee_name = employee_names.get(entry.employee)
		entry.balance = balances[key]
		data.append(entry)

	if not filters.transaction_type:
		# balances brought forward with no movement in the period
		data.extend(
			opening_row(key, value, from_date, employee_names) for key, value in opening.items() if value
		)
	return data


def opening_row(key, balance, from_date, employee_names):
	employee, account = key
	return frappe._dict(
		posting_date=from_date,
		employee=employee,
		employee_name=employee_names.get(employee),
		transaction_type=_("Opening"),
		account=account,
		balance=balance,
	)


def get_gl_entries(filters):
	conditions = ["gle.company = %(company)s", "gle.is_cancelled = 0", "gle.party_type = 'Employee'"]
	if filters.employee:
		conditions.append("gle.party = %(employee)s")
	if filters.to_date:
		conditions.append("gle.posting_date <= %(to_date)s")
	where = " and ".join(conditions)

	columns = """gle.name as gl_entry, gle.posting_date, gle.creation, gle.party as employee, gle.account,
		gle.debit, gle.credit, gle.voucher_type, gle.voucher_no"""

	return frappe.db.sql(
		f"""
		select {columns},
			if(je.gratuity_entry_type = 'Provision' and ifnull(ga.recalculation_of, '') != '',
				'Recalculation', je.gratuity_entry_type) as transaction_type,
			je.gratuity_accrual, je.gratuity, null as full_and_final_statement, je.user_remark as remarks
		from `tabGL Entry` gle
		inner join `tabJournal Entry` je on je.name = gle.voucher_no
		left join `tabGratuity Accrual` ga on ga.name = je.gratuity_accrual
		where {where} and gle.voucher_type = 'Journal Entry' and ifnull(je.gratuity_entry_type, '') != ''

		union all

		select {columns}, 'Gratuity', null, gle.voucher_no, null, gle.remarks
		from `tabGL Entry` gle
		where {where} and gle.voucher_type = 'Gratuity'

		union all

		select {columns}, 'Gratuity Payment', null, gle.against_voucher, null, gle.remarks
		from `tabGL Entry` gle
		where {where} and gle.voucher_type = 'Payment Entry' and gle.against_voucher_type = 'Gratuity'

		union all

		select {columns}, 'Final Settlement', null,
			(select fos.reference_document from `tabFull and Final Outstanding Statement` fos
				where fos.parent = fnf.name and fos.parentfield = 'payables'
				and fos.reference_document_type = 'Gratuity' and fos.account = gle.account
				order by fos.idx limit 1),
			fnf.name, gle.remarks
		from `tabGL Entry` gle
		inner join (
			select distinct parent, reference_name from `tabJournal Entry Account`
			where reference_type = 'Full and Final Statement'
		) jea on jea.parent = gle.voucher_no
		inner join `tabFull and Final Statement` fnf
			on fnf.name = jea.reference_name and fnf.employee = gle.party
		where {where} and gle.voucher_type = 'Journal Entry'
			and exists (
				select 1 from `tabFull and Final Outstanding Statement` fos
				where fos.parent = fnf.name and fos.parentfield = 'payables'
				and fos.reference_document_type = 'Gratuity' and fos.account = gle.account
			)

		order by employee, posting_date, creation, gl_entry
		""",
		filters,
		as_dict=True,
	)


def get_columns():
	return [
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{
			"label": _("Employee"),
			"fieldname": "employee",
			"fieldtype": "Link",
			"options": "Employee",
			"width": 130,
		},
		{"label": _("Employee Name"), "fieldname": "employee_name", "fieldtype": "Data", "width": 150},
		{"label": _("Transaction Type"), "fieldname": "transaction_type", "fieldtype": "Data", "width": 140},
		{
			"label": _("Account"),
			"fieldname": "account",
			"fieldtype": "Link",
			"options": "Account",
			"width": 180,
		},
		{"label": _("Debit"), "fieldname": "debit", "fieldtype": "Currency", "width": 110},
		{"label": _("Credit"), "fieldname": "credit", "fieldtype": "Currency", "width": 110},
		{"label": _("Balance (Cr)"), "fieldname": "balance", "fieldtype": "Currency", "width": 120},
		{
			"label": _("Voucher Type"),
			"fieldname": "voucher_type",
			"fieldtype": "Link",
			"options": "DocType",
			"width": 120,
		},
		{
			"label": _("Voucher No"),
			"fieldname": "voucher_no",
			"fieldtype": "Dynamic Link",
			"options": "voucher_type",
			"width": 170,
		},
		{
			"label": _("Gratuity Accrual"),
			"fieldname": "gratuity_accrual",
			"fieldtype": "Link",
			"options": "Gratuity Accrual",
			"width": 150,
		},
		{
			"label": _("Gratuity"),
			"fieldname": "gratuity",
			"fieldtype": "Link",
			"options": "Gratuity",
			"width": 150,
		},
		{
			"label": _("Full and Final Statement"),
			"fieldname": "full_and_final_statement",
			"fieldtype": "Link",
			"options": "Full and Final Statement",
			"width": 160,
		},
		{"label": _("Remarks"), "fieldname": "remarks", "fieldtype": "Data", "width": 250},
	]
