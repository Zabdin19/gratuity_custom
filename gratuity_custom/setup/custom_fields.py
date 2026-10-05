from frappe import _

# Journal Entry classification written by gratuity_custom. Read by the Employee Gratuity Ledger.
JE_ENTRY_TYPES = ("Opening Reversal", "Provision", "Provision Reversal", "Provision Release")


def get_custom_fields():
	"""Custom fields added to standard DocTypes. Created/updated idempotently on install and migrate."""
	return {
		"Company": [
			{
				"fieldname": "gratuity_provision_section",
				"fieldtype": "Section Break",
				"label": _("Gratuity Provision"),
				"insert_after": "default_payroll_payable_account",
				"collapsible": 1,
			},
			{
				"depends_on": "eval:!doc.__islocal",
				"fieldname": "gratuity_expense_account",
				"fieldtype": "Link",
				"label": _("Gratuity Expense Account"),
				"options": "Account",
				"no_copy": 1,
				"insert_after": "gratuity_provision_section",
				"description": _("Debited by monthly gratuity provision entries."),
			},
			{
				"depends_on": "eval:!doc.__islocal",
				"fieldname": "gratuity_expense_reversal_account",
				"fieldtype": "Link",
				"label": _("Gratuity Expense Reversal Account"),
				"options": "Account",
				"no_copy": 1,
				"insert_after": "gratuity_expense_account",
				"description": _("Credited when a gratuity provision is reversed or released."),
			},
			{
				"fieldname": "gratuity_provision_column_break",
				"fieldtype": "Column Break",
				"insert_after": "gratuity_expense_reversal_account",
			},
			{
				"depends_on": "eval:!doc.__islocal",
				"fieldname": "gratuity_provision_account",
				"fieldtype": "Link",
				"label": _("Gratuity Provision Account"),
				"options": "Account",
				"no_copy": 1,
				"insert_after": "gratuity_provision_column_break",
				"description": _(
					"Liability account with a blank Account Type. Provision lines carry the Employee as party."
				),
			},
			{
				"depends_on": "eval:!doc.__islocal",
				"fieldname": "default_gratuity_rule",
				"fieldtype": "Link",
				"label": _("Default Gratuity Rule"),
				"options": "Gratuity Rule",
				"no_copy": 1,
				"insert_after": "gratuity_provision_account",
			},
		],
		"Journal Entry": [
			{
				"depends_on": "eval:doc.gratuity_entry_type",
				"fieldname": "gratuity_entry_type",
				"fieldtype": "Select",
				"label": _("Gratuity Entry Type"),
				"options": "\n" + "\n".join(JE_ENTRY_TYPES),
				"read_only": 1,
				"no_copy": 1,
				"insert_after": "reversal_of",
			},
			{
				"depends_on": "eval:doc.gratuity_accrual",
				"fieldname": "gratuity_accrual",
				"fieldtype": "Link",
				"label": _("Gratuity Accrual"),
				"options": "Gratuity Accrual",
				"read_only": 1,
				"no_copy": 1,
				"search_index": 1,
				"insert_after": "gratuity_entry_type",
			},
			{
				"depends_on": "eval:doc.gratuity",
				"fieldname": "gratuity",
				"fieldtype": "Link",
				"label": _("Gratuity"),
				"options": "Gratuity",
				"read_only": 1,
				"no_copy": 1,
				"search_index": 1,
				"insert_after": "gratuity_accrual",
			},
		],
		"Gratuity": [
			{
				"depends_on": "eval:doc.provision_release_journal_entry",
				"fieldname": "provision_release_journal_entry",
				"fieldtype": "Link",
				"label": _("Provision Release Journal Entry"),
				"options": "Journal Entry",
				"read_only": 1,
				"no_copy": 1,
				"allow_on_submit": 1,
				"insert_after": "payable_account",
			},
		],
	}
