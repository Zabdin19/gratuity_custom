// Immediate feedback only; the server-side validate hook is authoritative.
frappe.ui.form.on("Full and Final Statement", {
	refresh(frm) {
		if (frm.is_new() && frm.doc.employee && frm.doc.company) {
			fill_gratuity_rows(frm);
		}
	},
});

frappe.ui.form.on("Full and Final Outstanding Statement", {
	reference_document(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.reference_document_type !== "Gratuity" || !row.reference_document) return;
		frappe.db.get_value("Gratuity", row.reference_document, "status").then(({ message }) => {
			if (message && message.status === "Paid") {
				frappe.model.set_value(cdt, cdn, "status", "Settled");
			}
		});
	},
});

function fill_gratuity_rows(frm) {
	frappe
		.call({
			method: "gratuity_custom.events.full_and_final_statement.get_settlement_gratuities",
			args: { employee: frm.doc.employee, company: frm.doc.company },
		})
		.then(({ message: gratuities }) => {
			const rows = (frm.doc.payables || []).filter(
				(r) => r.reference_document_type === "Gratuity"
			);
			const referenced = new Set(rows.map((r) => r.reference_document).filter(Boolean));
			const placeholders = rows.filter((r) => !r.reference_document);

			(gratuities || [])
				.filter((name) => !referenced.has(name))
				.forEach((name) => {
					const row =
						placeholders.shift() ||
						frm.add_child("payables", {
							component: "Gratuity",
							reference_document_type: "Gratuity",
							status: "Unsettled",
						});
					// triggers the standard amount/account fetch and the status sync above
					frappe.model.set_value(row.doctype, row.name, "reference_document", name);
				});
			frm.refresh_field("payables");
		});
}
