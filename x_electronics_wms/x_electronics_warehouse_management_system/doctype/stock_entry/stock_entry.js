// Copyright (c) 2026
// For license information, please see license.txt

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

                frappe.model.set_value(
                    row.doctype,
                    row.name,
                    "rate",
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

            // Re-evaluate row rate
            fetch_rate_for_row(
                frm,
                row.doctype,
                row.name
            );
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

                fetch_rate_for_row(
                    frm,
                    row.doctype,
                    row.name
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

        if (frm.doc.stock_entry_type === "Receipt") {

            frappe.model.set_value(
                cdt,
                cdn,
                "t_warehouse",
                frm.doc.default_target_wh
            );
        }

        else if (frm.doc.stock_entry_type === "Consume") {

            frappe.model.set_value(
                cdt,
                cdn,
                "s_warehouse",
                frm.doc.default_source_wh
            );
        }

        else if (frm.doc.stock_entry_type === "Transfer") {

            frappe.model.set_value(
                cdt,
                cdn,
                "s_warehouse",
                frm.doc.default_source_wh
            );

            frappe.model.set_value(
                cdt,
                cdn,
                "t_warehouse",
                frm.doc.default_target_wh
            );
        }

        fetch_rate_for_row(frm, cdt, cdn);
    },

    // REACTIVE RATE FETCHING

    item(frm, cdt, cdn) {
        fetch_rate_for_row(frm, cdt, cdn);
    },

    s_warehouse(frm, cdt, cdn) {
        fetch_rate_for_row(frm, cdt, cdn);
    }
});



// RATE FETCHING

function fetch_rate_for_row(frm, cdt, cdn) {

    const row = locals[cdt][cdn];
    const type = frm.doc.stock_entry_type;

    if (type === "Receipt") {
        frappe.model.set_value(cdt, cdn, "rate", null);
        return;
    }

    if (!row.item || !row.s_warehouse) {
        return;
    }

    frappe.call({
        method: "x_electronics_wms.x_electronics_warehouse_management_system.doctype.stock_entry.stock_entry.get_item_rate",
        args: {
            item: row.item,
            s_warehouse: row.s_warehouse,
            stock_entry_type: type,
            qty: row.qty || 1
        },
        callback(r) {
            if (r.message !== undefined) {
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

    const grid = frm.fields_dict.items.grid;

    // Parent fields

    frm.set_df_property(
        "default_source_wh",
        "hidden",
        is_receipt
    );

    frm.set_df_property(
        "default_target_wh",
        "hidden",
        is_consume
    );

    // Child fields

    grid.update_docfield_property(
        "s_warehouse",
        "hidden",
        is_receipt ? 1 : 0
    );

    grid.update_docfield_property(
        "t_warehouse",
        "hidden",
        is_consume ? 1 : 0
    );

    // Rate field behavior

    grid.update_docfield_property(
        "rate",
        "read_only",
        is_receipt ? 0 : 1
    );

    grid.update_docfield_property(
        "rate",
        "reqd",
        is_receipt ? 1 : 0
    );

    frm.refresh_field("items");

    if (grid.refresh) {
        grid.refresh();
    }

    if (grid.reset_grid) {
        grid.reset_grid();
    }

    frm.refresh_fields([
        "default_source_wh",
        "default_target_wh"
    ]);
}


