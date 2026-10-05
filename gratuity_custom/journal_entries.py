"""Standard Journal Entries for gratuity provision accounting.

Provision lines are employee-wise (party = Employee on the untyped liability account); the matching
profit-and-loss lines are grouped by cost center.
"""

from collections import defaultdict

import frappe
from frappe.utils import flt

# Direction of an entry, from the provision account's point of view.
ACCRUE = "accrue"  # Dr Profit and Loss / Cr Provision
RELEASE = "release"  # Dr Provision / Cr Profit and Loss


def build_provision_lines(provision_account, pl_account, entries, direction, remark=None):
	"""Journal Entry account rows for `entries` = [(employee, cost_center, amount), ...].

	A negative amount (e.g. reversing an unexpected debit balance) is posted on the opposite side.
	"""
	precision = frappe.get_precision("Journal Entry Account", "debit_in_account_currency")
	lines, pl_by_cost_center = [], defaultdict(float)

	for employee, cost_center, amount in entries:
		amount = flt(amount, precision)
		if not amount:
			continue
		provision_debit = amount if direction == RELEASE else -amount
		lines.append(
			{
				"account": provision_account,
				"party_type": "Employee",
				"party": employee,
				"cost_center": cost_center,
				**_side(provision_debit, precision),
				"user_remark": remark,
			}
		)
		pl_by_cost_center[cost_center] -= provision_debit

	for cost_center, pl_debit in pl_by_cost_center.items():
		if flt(pl_debit, precision):
			lines.append(
				{
					"account": pl_account,
					"cost_center": cost_center,
					**_side(pl_debit, precision),
					"user_remark": remark,
				}
			)
	return lines


def _side(debit, precision):
	debit = flt(debit, precision)
	return {
		"debit_in_account_currency": debit if debit > 0 else 0,
		"credit_in_account_currency": -debit if debit < 0 else 0,
	}


def submit_gratuity_journal_entry(
	company,
	posting_date,
	entry_type,
	lines,
	user_remark,
	gratuity_accrual=None,
	gratuity=None,
):
	"""Create and submit a system-generated gratuity Journal Entry.

	Permissions are checked on the originating action (Gratuity Accrual submit / reverse, Gratuity
	submit), so the entry itself is created with ignore_permissions, as standard HRMS does for the GL
	entries of Gratuity and Payroll Entry.
	"""
	if not lines:
		return None
	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.company = company
	je.posting_date = posting_date
	je.user_remark = user_remark
	je.gratuity_entry_type = entry_type
	je.gratuity_accrual = gratuity_accrual
	je.gratuity = gratuity
	je.set("accounts", lines)
	return _insert_and_submit(je)


def _insert_and_submit(je):
	je.flags.gratuity_custom_managed = True
	je.flags.ignore_permissions = True
	je.insert()
	je.submit()
	return je.name


def submit_reversal_of(
	journal_entry, posting_date, pl_account, reversal_account, user_remark, gratuity_accrual
):
	"""Reverse one specific Journal Entry with the standard ERPNext mapper (sets `reversal_of`).

	The mapper swaps debit and credit on the same accounts; the profit-and-loss side is then moved
	from `pl_account` to `reversal_account` (Dr Provision / Cr Expense Reversal).
	"""
	from erpnext.accounts.doctype.journal_entry.journal_entry import make_reverse_journal_entry

	je = make_reverse_journal_entry(journal_entry)
	je.posting_date = posting_date
	je.user_remark = user_remark
	je.gratuity_entry_type = "Provision Reversal"
	je.gratuity_accrual = gratuity_accrual
	for row in je.accounts:
		if row.account == pl_account:
			row.account = reversal_account
	return _insert_and_submit(je)


def cancel_gratuity_journal_entry(journal_entry):
	je = frappe.get_doc("Journal Entry", journal_entry)
	if je.docstatus != 1:
		return
	je.flags.gratuity_custom_managed = True
	je.flags.ignore_permissions = True
	je.cancel()
