// Copyright (c) 2026, Eammon Kiprotich and contributors
// For license information, please see license.txt

frappe.ui.form.on("Item", {
	refresh(frm) {
        if (frm.is_new()) {
            frappe.db.get_single_value(
                "Stock Settings",
                "default_valuation_method"
            ).then((value) => {
                if (value) {
                    frm.set_value("valuation_method", value);
                }
            });
        }

	},
});
