import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from gratuity_custom.setup.custom_fields import get_custom_fields


def after_install():
	make_custom_fields()


def after_migrate():
	make_custom_fields()


def make_custom_fields():
	create_custom_fields(get_custom_fields(), update=True)


def before_uninstall():
	"""Remove only the custom fields this app created."""
	for doctype, fields in get_custom_fields().items():
		for df in fields:
			name = frappe.db.get_value("Custom Field", {"dt": doctype, "fieldname": df["fieldname"]})
			if name:
				frappe.delete_doc("Custom Field", name, ignore_permissions=True)
		frappe.clear_cache(doctype=doctype)
