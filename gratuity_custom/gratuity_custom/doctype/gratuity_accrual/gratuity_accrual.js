// Copyright (c) 2026, zainulabdin and contributors
// For license information, please see license.txt

frappe.ui.form.on("Gratuity Accrual", {
	setup(frm) {
		const account_query = (filters) => () => ({
			filters: { company: frm.doc.company, is_group: 0, ...filters },
		});
		frm.set_query("expense_account", account_query({ root_type: "Expense" }));
		frm.set_query(
			"expense_reversal_account",
			account_query({ report_type: "Profit and Loss" })
		);
		frm.set_query(
			"provision_account",
			account_query({ root_type: "Liability", account_type: ["is", "not set"] })
		);
		frm.set_query("cost_center", account_query({}));
		frm.set_query("cost_center", "employees", account_query({}));
		frm.set_query("gratuity_rule", () => ({ filters: { disable: 0 } }));
		frm.set_query("employee", "employees", () => ({
			filters: { company: frm.doc.company, status: ["in", ["Active", "Left"]] },
		}));
	},

	refresh(frm) {
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Get Employees"), () => {
				frm.call({ method: "get_employees", doc: frm.doc, freeze: true }).then(() => {
					frm.refresh_fields();
					frm.dirty();
				});
			});
		}

		if (
			frm.doc.docstatus === 1 &&
			frm.doc.status === "Posted" &&
			frm.doc.provision_journal_entry
		) {
			frm.add_custom_button(__("Reverse Provision"), () => reverse_provision(frm));
		}

		if (frm.doc.docstatus === 1 && frm.doc.status === "Reversed") {
			frm.add_custom_button(__("Create Recalculation"), () => {
				frm.call({ method: "create_recalculation", doc: frm.doc, freeze: true }).then(
					({ message }) =>
						message && frappe.set_route("Form", "Gratuity Accrual", message)
				);
			});
		}
	},

	company(frm) {
		if (!frm.doc.company) return;
		frappe.db
			.get_value("Company", frm.doc.company, [
				"gratuity_expense_account",
				"gratuity_expense_reversal_account",
				"gratuity_provision_account",
				"default_gratuity_rule",
				"cost_center",
			])
			.then(({ message: c }) => {
				frm.set_value({
					expense_account: c.gratuity_expense_account,
					expense_reversal_account: c.gratuity_expense_reversal_account,
					provision_account: c.gratuity_provision_account,
					gratuity_rule: c.default_gratuity_rule,
					cost_center: c.cost_center,
				});
			});
	},

	from_date(frm) {
		if (frm.doc.from_date && !frm.doc.to_date) {
			const to_date = moment(frm.doc.from_date).endOf("month").format("YYYY-MM-DD");
			frm.set_value({ to_date, posting_date: to_date });
		}
	},
});

function reverse_provision(frm) {
	frappe.prompt(
		{
			fieldname: "posting_date",
			fieldtype: "Date",
			label: __("Reversal Posting Date"),
			reqd: 1,
			default: frappe.datetime.get_today(),
		},
		({ posting_date }) => {
			frm.call({
				method: "reverse_provision",
				doc: frm.doc,
				args: { posting_date },
				freeze: true,
			}).then(() => frm.reload_doc());
		},
		__("Reverse Provision"),
		__("Reverse")
	);
}
