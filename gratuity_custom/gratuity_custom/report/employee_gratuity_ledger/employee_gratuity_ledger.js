// Copyright (c) 2026, zainulabdin and contributors
// For license information, please see license.txt

frappe.query_reports["Employee Gratuity Ledger"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "employee",
			label: __("Employee"),
			fieldtype: "Link",
			options: "Employee",
			get_query: () => ({
				filters: { company: frappe.query_report.get_filter_value("company") },
			}),
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "transaction_type",
			label: __("Transaction Type"),
			fieldtype: "Select",
			options: [
				"",
				"Opening Reversal",
				"Provision",
				"Recalculation",
				"Provision Reversal",
				"Provision Release",
				"Gratuity",
				"Gratuity Payment",
				"Final Settlement",
			],
		},
	],
};
