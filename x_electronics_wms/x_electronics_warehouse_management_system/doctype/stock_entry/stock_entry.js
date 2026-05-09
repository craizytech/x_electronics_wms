// Copyright (c) 2026, Eammon Kiprotich and contributors
// For license information, please see license.txt

frappe.ui.form.on("Stock Entry", {

    setup(frm) {
        // Prefill only for new unsaved docs
        if (frm.is_new()) {

            // Current date
            if (!frm.doc.posting_date) {
                frm.set_value(
                    "posting_date",
                    frappe.datetime.get_today()
                );
            }

            // Current time
            if (!frm.doc.posting_time) {
                frm.set_value(
                    "posting_time",
                    frappe.datetime.now_time()
                );
            }
        }
    },

    refresh(frm) {
        toggle_rate_required(frm);
    },

    stock_entry_type(frm) {
        toggle_rate_required(frm);
    }
});


function toggle_rate_required(frm) {

    let required =
        frm.doc.stock_entry_type === "Consume" ||
        frm.doc.stock_entry_type === "Receipt";

    frm.fields_dict.items.grid.update_docfield_property(
        "rate",
        "reqd",
        required ? 1 : 0
    );

    frm.refresh_field("items");
}
