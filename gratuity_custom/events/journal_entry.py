"""Guards keeping gratuity Journal Entries consistent with their source documents.

Gratuity entries are created only by Gratuity Accrual (provision / reversal) and by the Gratuity
final-settlement hook (provision release). Reversing or cancelling them directly would leave the
source document's status and links out of step, so those paths are blocked.
"""

import frappe
from frappe import _, bold


def validate(doc, method=None):
	if doc.flags.gratuity_custom_managed:
		return

	if doc.get("gratuity_entry_type") or doc.get("gratuity_accrual") or doc.get("gratuity"):
		frappe.throw(
			_("Gratuity journal entries are created only by Gratuity Accrual and Gratuity documents"),
			title=_("Not Allowed"),
		)

	if doc.get("reversal_of"):
		source = frappe.db.get_value(
			"Journal Entry",
			doc.reversal_of,
			["gratuity_entry_type", "gratuity_accrual", "gratuity"],
			as_dict=True,
		)
		if source and source.gratuity_entry_type:
			frappe.throw(
				_(
					"Journal Entry {0} is a gratuity {1} entry. Use Reverse Provision on Gratuity Accrual {2} instead."
				).format(
					bold(doc.reversal_of),
					source.gratuity_entry_type,
					bold(source.gratuity_accrual or source.gratuity),
				),
				title=_("Not Allowed"),
			)


def before_cancel(doc, method=None):
	if doc.flags.gratuity_custom_managed or not doc.get("gratuity_entry_type"):
		return
	frappe.throw(
		_(
			"Journal Entry {0} was posted by {1}. Reverse it from the Gratuity Accrual, or cancel the Gratuity for a provision release."
		).format(bold(doc.name), bold(doc.gratuity_accrual or doc.gratuity)),
		title=_("Not Allowed"),
	)
