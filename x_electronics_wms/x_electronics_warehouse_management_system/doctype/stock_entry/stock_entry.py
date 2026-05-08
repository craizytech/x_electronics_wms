# Copyright (c) 2026, Eammon Kiprotich and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import nowtime

class StockEntry(Document):

    # Validation

    def validate(self):
        self.set_posting_time()
        self.validate_items()
        self.validate_warehouses()

    def set_posting_time(self):
        if not self.posting_time:
            self.posting_time = nowtime()

    def validate_items(self):
        if not self.items:
            frappe.throw(_("Please add at least one item."))

        for row in self.items:
            if not row.qty or row.qty <= 0:
                frappe.throw(_(f"Row {row.idx}: Qty must be greater than zero."))

            if self.stock_entry_type == "Receipt":
                if not row.t_warehouse:
                    frappe.throw(_(f"Row {row.idx}: Target Warehouse is required for Receipt."))
                if row.s_warehouse:
                    frappe.throw(_(f"Row {row.idx}: Source Warehouse must be empty for Receipt."))
                if not row.rate or row.rate <= 0:
                    frappe.throw(_(f"Row {row.idx}: Rate is required for Receipt."))

            elif self.stock_entry_type == "Consume":
                if not row.s_warehouse:
                    frappe.throw(_(f"Row {row.idx}: Source Warehouse is required for Consume."))
                if row.t_warehouse:
                    frappe.throw(_(f"Row {row.idx}: Target Warehouse must be empty for Consume."))
                if not row.rate or row.rate <= 0:
                    frappe.throw(_(f"Row {row.idx}: Rate is required for Consume."))

            elif self.stock_entry_type == "Transfer":
                if not row.s_warehouse:
                    frappe.throw(_(f"Row {row.idx}: Source Warehouse is required for Transfer."))
                if not row.t_warehouse:
                    frappe.throw(_(f"Row {row.idx}: Target Warehouse is required for Transfer."))
                if row.s_warehouse == row.t_warehouse:
                    frappe.throw(_(f"Row {row.idx}: Source and Target Warehouse cannot be the same."))

    def validate_warehouses(self):
        """Ensure no group warehouse is used as a transaction warehouse."""
        warehouses = set()
        for row in self.items:
            if row.s_warehouse:
                warehouses.add(row.s_warehouse)
            if row.t_warehouse:
                warehouses.add(row.t_warehouse)

        for wh in warehouses:
            is_group = frappe.db.get_value("Warehouse", wh, "is_group")
            if is_group:
                frappe.throw(_(f"Warehouse '{wh}' is a group warehouse and cannot be used in transactions."))

    # Submit / Cancel

    def on_submit(self):
        self.make_ledger_entries()

    def on_cancel(self):
        self.cancel_ledger_entries()

    # Ledger creation

    def make_ledger_entries(self):
        posting_dt = f"{self.posting_date} {self.posting_time or '00:00:00'}"

        for row in self.items:
            # Incoming leg (Receipt / Transfer in)
            if row.t_warehouse:
                incoming_rate = self._get_incoming_rate(row)
                self._create_sle(
                    item=row.item,
                    warehouse=row.t_warehouse,
                    qty_change=row.qty,
                    incoming_rate=incoming_rate,
                    posting_dt=posting_dt,
                )

            # Outgoing leg (Consume / Transfer out)
            if row.s_warehouse:
                outgoing_rate = self._get_outgoing_rate(row.item, row.s_warehouse)
                self._create_sle(
                    item=row.item,
                    warehouse=row.s_warehouse,
                    qty_change=-row.qty,
                    incoming_rate=outgoing_rate,
                    posting_dt=posting_dt,
                )

    def _get_incoming_rate(self, row):
        """
        For Receipt: use the rate entered by the user.
        For Transfer: use the current valuation rate of the source warehouse.
        """
        if self.stock_entry_type == "Receipt":
            return row.rate

        if self.stock_entry_type == "Transfer":
            return self._get_outgoing_rate(row.item, row.s_warehouse)

        return 0.0

    def _get_outgoing_rate(self, item, warehouse):
        """
        Current moving-average valuation rate for an item in a warehouse.
        Fully stateless — derived from ledger via single SQL query.
        """
        result = frappe.db.sql("""
            SELECT
                COALESCE(SUM(qty_change), 0) AS total_qty,
                COALESCE(SUM(stock_value), 0) AS total_value
            FROM `tabStock Ledger Entry`
            WHERE
                item      = %(item)s
                AND warehouse = %(warehouse)s
                AND docstatus = 1
        """, {"item": item, "warehouse": warehouse}, as_dict=True)

        row = result[0] if result else None
        if row and row.total_qty and row.total_qty > 0:
            return row.total_value / row.total_qty

        return 0.0

    def _create_sle(self, item, warehouse, qty_change, incoming_rate, posting_dt):
        """
        Create one Stock Ledger Entry using moving average valuation.

        Incoming:
            new_rate  = (current_value + qty_change * incoming_rate) / new_qty
            new_value = qty_change * incoming_rate

        Outgoing:
            rate      = current moving average
            value     = qty_change * current_rate  (negative)
        """
        # Current state — derived from ledger, never stored
        current = frappe.db.sql("""
            SELECT
                COALESCE(SUM(qty_change), 0)  AS qty,
                COALESCE(SUM(stock_value), 0) AS value
            FROM `tabStock Ledger Entry`
            WHERE
                item      = %(item)s
                AND warehouse = %(warehouse)s
                AND docstatus = 1
        """, {"item": item, "warehouse": warehouse}, as_dict=True)[0]

        current_qty   = current.qty   or 0.0
        current_value = current.value or 0.0

        if qty_change > 0:
            # Incoming: recalculate moving average
            new_qty            = current_qty + qty_change
            new_value          = current_value + (qty_change * incoming_rate)
            valuation_rate     = new_value / new_qty if new_qty else incoming_rate
            stock_value_change = qty_change * incoming_rate
        else:
            # Outgoing: use existing moving average rate
            current_rate       = (current_value / current_qty) if current_qty else 0.0
            valuation_rate     = current_rate
            stock_value_change = qty_change * current_rate  # already negative

        sle = frappe.get_doc({
            "doctype":          "Stock Ledger Entry",
            "item":             item,
            "warehouse":        warehouse,
            "posting_datetime": posting_dt,
            "qty_change":       qty_change,
            "valuation_rate":   valuation_rate,
            "stock_value":      stock_value_change,
            "voucher_type":     self.doctype,
            "voucher_no":       self.name,
        })
        sle.flags.ignore_permissions = True
        sle.submit()

    # Cancellation

    def cancel_ledger_entries(self):
        sle_names = frappe.get_all(
            "Stock Ledger Entry",
            filters={"voucher_type": self.doctype, "voucher_no": self.name},
            pluck="name",
        )
        for sle_name in sle_names:
            sle = frappe.get_doc("Stock Ledger Entry", sle_name)
            sle.flags.ignore_permissions = True
            sle.cancel()
