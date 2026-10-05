from gratuity_custom.accounts import validate_gratuity_accounts
from gratuity_custom.calculation import validate_gratuity_rule


def validate(doc, method=None):
	validate_gratuity_accounts(
		doc.name,
		doc.get("gratuity_expense_account"),
		doc.get("gratuity_expense_reversal_account"),
		doc.get("gratuity_provision_account"),
	)

	if doc.get("default_gratuity_rule"):
		validate_gratuity_rule(doc.default_gratuity_rule)
