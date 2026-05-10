
frappe.ui.form.on("Stock Entry", {

    setup(frm) {
        if (frm.is_new()) {

            if (!frm.doc.posting_date) {
                frm.set_value(
                    "posting_date",
                    frappe.datetime.get_today()
                );
            }

            if (!frm.doc.posting_time) {
                frm.set_value(
                    "posting_time",
                    frappe.datetime.now_time()
                );
            }
        }
    },

    refresh(frm) {
        apply_stock_entry_ui_state(frm);
    },

    stock_entry_type(frm) {

        apply_stock_entry_ui_state(frm);

        const is_receipt =
            frm.doc.stock_entry_type === "Receipt";

        const is_consume =
            frm.doc.stock_entry_type === "Consume";

        // Clear incompatible warehouse fields

        frm.doc.items.forEach(row => {

            // Receipt:
            // no source warehouse
            if (is_receipt) {

                frappe.model.set_value(
                    row.doctype,
                    row.name,
                    "s_warehouse",
                    null
                );
            }

            // Consume:
            // no target warehouse
            if (is_consume) {

                frappe.model.set_value(
                    row.doctype,
                    row.name,
                    "t_warehouse",
                    null
                );
            }
        });

        frm.refresh_field("items");
    },


    // Propagate changed defaults

    default_source_wh(frm) {

        frm.doc.items.forEach(row => {

            if (
                frm.doc.stock_entry_type === "Consume" ||
                frm.doc.stock_entry_type === "Transfer"
            ) {

                frappe.model.set_value(
                    row.doctype,
                    row.name,
                    "s_warehouse",
                    frm.doc.default_source_wh
                );
            }
        });
    },

    default_target_wh(frm) {

        frm.doc.items.forEach(row => {

            if (
                frm.doc.stock_entry_type === "Receipt" ||
                frm.doc.stock_entry_type === "Transfer"
            ) {

                frappe.model.set_value(
                    row.doctype,
                    row.name,
                    "t_warehouse",
                    frm.doc.default_target_wh
                );
            }
        });
    }
});



// Child Table Events

frappe.ui.form.on("Stock Entry Detail", {

    items_add(frm, cdt, cdn) {
        const row = locals[cdt][cdn];

        if (frm.doc.stock_entry_type === "Receipt") {
            frappe.model.set_value(cdt, cdn, "t_warehouse", frm.doc.default_target_wh);
        }
        else if (frm.doc.stock_entry_type === "Consume") {
            frappe.model.set_value(cdt, cdn, "s_warehouse", frm.doc.default_source_wh);
        }
        else if (frm.doc.stock_entry_type === "Transfer") {
            frappe.model.set_value(cdt, cdn, "s_warehouse", frm.doc.default_source_wh);
            frappe.model.set_value(cdt, cdn, "t_warehouse", frm.doc.default_target_wh);
        }
    },

    // ==================== AUTO FETCH RATE ====================
    item(frm, cdt, cdn) {
        fetch_rate_for_row(frm, cdt, cdn);
    },

    s_warehouse(frm, cdt, cdn) {
        fetch_rate_for_row(frm, cdt, cdn);
    }
});


// Rate Fetching Function
function fetch_rate_for_row(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    const type = frm.doc.stock_entry_type;

    // Only auto-fetch for Consume and Transfer
    if (type === "Receipt" || !row.item || !row.s_warehouse) {
        return;
    }

    // Call server method
    frappe.call({
        method: "get_item_rate",           // since it's in StockEntry class
        doc: frm.doc,                      // Important: passes self
        args: {
            item: row.item,
            s_warehouse: row.s_warehouse,
            stock_entry_type: type
        },
        callback: function(r) {
            if (r.message) {
                frappe.model.set_value(cdt, cdn, "rate", r.message);
            }
        }
    });
}



// UI STATE CONTROLLER

function apply_stock_entry_ui_state(frm) {
    const type = frm.doc.stock_entry_type || "";
    
    const is_receipt = type === "Receipt";
    const is_consume = type === "Consume";
    // Transfer shows both

    const grid = frm.fields_dict.items.grid;

    // Updating the parent
    frm.set_df_property("default_source_wh", "hidden", is_receipt);
    frm.set_df_property("default_target_wh", "hidden", is_consume);

    // updating child
    grid.update_docfield_property("s_warehouse", "hidden", is_receipt ? 1 : 0);
    grid.update_docfield_property("t_warehouse", "hidden", is_consume ? 1 : 0);

    // rate field
    grid.update_docfield_property("rate", "read_only", is_receipt ? 0 : 1);
    grid.update_docfield_property("rate", "reqd", is_receipt ? 1 : 0);

    frm.refresh_field("items");
    
    if (grid.refresh) grid.refresh();
    if (grid.reset_grid) grid.reset_grid();
    
    // Refresh parent fields too
    frm.refresh_fields(["default_source_wh", "default_target_wh"]);
}
