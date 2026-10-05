frappe.ui.form.on("Company", {
	setup(frm) {
		frm.set_query("gratuity_expense_account", () => ({
			filters: { company: frm.doc.name, is_group: 0, root_type: "Expense" },
		}));
		frm.set_query("gratuity_expense_reversal_account", () => ({
			filters: { company: frm.doc.name, is_group: 0, report_type: "Profit and Loss" },
		}));
		frm.set_query("gratuity_provision_account", () => ({
			filters: {
				company: frm.doc.name,
				is_group: 0,
				root_type: "Liability",
				account_type: ["is", "not set"],
			},
		}));
		frm.set_query("default_gratuity_rule", () => ({ filters: { disable: 0 } }));
	},
});
