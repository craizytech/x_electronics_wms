# Copyright (c) 2026, Eammon Kiprotich and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import nowtime


class SalesInvoice(Document):
    # begin: auto-generated types
    # This code is auto-generated. Do not modify anything in this block.

    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from frappe.types import DF
        from x_electronics_wms.x_electronics_warehouse_management_system.doctype.sales_invoice_detail.sales_invoice_detail import SalesInvoiceDetail

        amended_from: DF.Link | None
        customer: DF.Data
        gross_profit: DF.Currency
        items: DF.Table[SalesInvoiceDetail]
        posting_date: DF.Date
        posting_time: DF.Time | None
        total_amount: DF.Currency
        total_cogs: DF.Currency
    # end: auto-generated types

    # Validation

    def validate(self):
        self.set_posting_time()
        self.validate_items()
        self.validate_stock_availability()
        self.calculate_amounts()

    def set_posting_time(self):
        if not self.posting_time:
            self.posting_time = nowtime()

    def validate_items(self):
        if not self.items:
            frappe.throw(_("Please add at least one item."))

        for row in self.items:
            if not row.qty or row.qty <= 0:
                frappe.throw(_(f"Row {row.idx}: Qty must be greater than zero."))
            if not row.rate or row.rate <= 0:
                frappe.throw(_(f"Row {row.idx}: Rate (selling price) must be greater than zero."))
            if not row.warehouse:
                frappe.throw(_(f"Row {row.idx}: Warehouse is required."))

            # Validate warehouse is not a group
            is_group = frappe.db.get_value("Warehouse", row.warehouse, "is_group")
            if is_group:
                frappe.throw(_(f"Row {row.idx}: '{row.warehouse}' is a group warehouse."))

    def validate_stock_availability(self):
        """
        Ensure sufficient stock exists in each warehouse for each item.
        Prevents selling stock that does not exist.
        """
        for row in self.items:
            current_qty = frappe.db.sql("""
                SELECT COALESCE(SUM(qty_change), 0) AS qty
                FROM `tabStock Ledger Entry`
                WHERE
                    item      = %(item)s
                    AND warehouse = %(warehouse)s
                    AND docstatus = 1
            """, {"item": row.item, "warehouse": row.warehouse},
            as_dict=True)[0].qty or 0.0

            if current_qty < row.qty:
                frappe.throw(_(
                    f"Row {row.idx}: Insufficient stock for '{row.item}' "
                    f"in '{row.warehouse}'. "
                    f"Available: {current_qty}, Required: {row.qty}."
                ))

    def calculate_amounts(self):
        """
        Compute row-level amounts and invoice totals.
        Valuation rate and COGS are fetched from the ledger
        so the user sees the expected profit before submitting.
        """
        total_amount = 0.0
        total_cogs   = 0.0

        for row in self.items:
            # user enters the selling price
            row.amount = (row.qty or 0) * (row.rate or 0)

            # Item name for display
            if row.item and not row.item_name:
                row.item_name = frappe.db.get_value("Item", row.item, "item_name")

            # Cost side — derived from ledger using the item's valuation method
            valuation_method = frappe.db.get_value(
                "Item", row.item, "valuation_method"
            ) or "Moving Average"

            row.valuation_rate = self._get_valuation_rate(
                row.item, row.warehouse, row.qty, valuation_method
            )
            row.cogs = row.qty * row.valuation_rate

            total_amount += row.amount
            total_cogs   += row.cogs

        self.total_amount = total_amount
        self.total_cogs   = total_cogs
        self.gross_profit = total_amount - total_cogs

    # Submit / Cancel

    def on_submit(self):
        self.make_ledger_entries()

    def on_cancel(self):
        self.cancel_ledger_entries()

    # Ledger creation
    def make_ledger_entries(self):
        """
        A Sales Invoice writes Stock Ledger Entries directly.
        No intermediate Stock Entry is created.

        Each sold item row creates one negative SLE (stock leaving).
        voucher_type = "Sales Invoice" so stock reports can distinguish
        sales from spoilage, transfers, and other consumption.
        """
        posting_dt = f"{self.posting_date} {self.posting_time or '00:00:00'}"

        for row in self.items:
            valuation_method = frappe.db.get_value(
                "Item", row.item, "valuation_method"
            ) or "Moving Average"

            outgoing_rate = self._get_valuation_rate(
                row.item, row.warehouse, row.qty, valuation_method
            )

            self._create_sle(
                item=row.item,
                warehouse=row.warehouse,
                qty_change=-row.qty,
                outgoing_rate=outgoing_rate,
                posting_dt=posting_dt,
                valuation_method=valuation_method,
            )

    def _get_valuation_rate(self, item, warehouse, qty, valuation_method):
        """Route to the correct valuation method for outgoing stock."""
        if valuation_method == "FIFO":
            return self._fifo_rate(item, warehouse, qty)
        elif valuation_method == "LIFO":
            return self._lifo_rate(item, warehouse, qty)
        else:
            return self._moving_average_rate(item, warehouse)

    def _moving_average_rate(self, item, warehouse):
        result = frappe.db.sql("""
            SELECT
                COALESCE(SUM(qty_change), 0)  AS total_qty,
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

    def _fifo_rate(self, item, warehouse, qty_to_consume):
        incoming_layers = frappe.db.sql("""
            SELECT qty_change AS qty, valuation_rate AS rate
            FROM `tabStock Ledger Entry`
            WHERE
                item      = %(item)s
                AND warehouse = %(warehouse)s
                AND qty_change > 0
                AND docstatus = 1
            ORDER BY posting_datetime ASC, creation ASC
        """, {"item": item, "warehouse": warehouse}, as_dict=True)

        already_consumed = frappe.db.sql("""
            SELECT COALESCE(SUM(qty_change), 0) AS total_out
            FROM `tabStock Ledger Entry`
            WHERE
                item      = %(item)s
                AND warehouse = %(warehouse)s
                AND qty_change < 0
                AND docstatus = 1
        """, {"item": item, "warehouse": warehouse}, as_dict=True)

        consumed_so_far = abs(already_consumed[0].total_out or 0.0)
        return self._consume_layers_in_order(incoming_layers, consumed_so_far, qty_to_consume)

    def _lifo_rate(self, item, warehouse, qty_to_consume):
        incoming_layers = frappe.db.sql("""
            SELECT qty_change AS qty, valuation_rate AS rate
            FROM `tabStock Ledger Entry`
            WHERE
                item      = %(item)s
                AND warehouse = %(warehouse)s
                AND qty_change > 0
                AND docstatus = 1
            ORDER BY posting_datetime DESC, creation DESC
        """, {"item": item, "warehouse": warehouse}, as_dict=True)

        already_consumed = frappe.db.sql("""
            SELECT COALESCE(SUM(qty_change), 0) AS total_out
            FROM `tabStock Ledger Entry`
            WHERE
                item      = %(item)s
                AND warehouse = %(warehouse)s
                AND qty_change < 0
                AND docstatus = 1
        """, {"item": item, "warehouse": warehouse}, as_dict=True)

        consumed_so_far = abs(already_consumed[0].total_out or 0.0)
        return self._consume_layers_in_order(incoming_layers, consumed_so_far, qty_to_consume)

    def _consume_layers_in_order(self, layers, already_consumed, qty_to_consume):
        remaining_to_burn    = already_consumed
        remaining_to_consume = qty_to_consume
        total_value = 0.0
        total_qty   = 0.0

        for layer in layers:
            layer_qty  = float(layer.qty  or 0)
            layer_rate = float(layer.rate or 0)
            if layer_qty <= 0:
                continue
            if remaining_to_burn > 0:
                burned          = min(layer_qty, remaining_to_burn)
                layer_qty      -= burned
                remaining_to_burn -= burned
            if layer_qty > 0 and remaining_to_consume > 0:
                consumable       = min(layer_qty, remaining_to_consume)
                total_value     += consumable * layer_rate
                total_qty       += consumable
                remaining_to_consume -= consumable
            if remaining_to_consume <= 0:
                break

        return total_value / total_qty if total_qty > 0 else 0.0

    def _create_sle(self, item, warehouse, qty_change, outgoing_rate,
                    posting_dt, valuation_method):
        """
        Write one Stock Ledger Entry for a sold item.
        qty_change is always negative (stock leaving).
        voucher_type = "Sales Invoice" distinguishes this from
        Stock Entry consumption in all reports.
        """
        sle = frappe.get_doc({
            "doctype":          "Stock Ledger Entry",
            "item":             item,
            "warehouse":        warehouse,
            "posting_datetime": posting_dt,
            "qty_change":       qty_change,
            "valuation_rate":   outgoing_rate,
            "stock_value":      qty_change * outgoing_rate,  # negative
            "voucher_type":     "Sales Invoice",
            "voucher_no":       self.name,
            "valuation_method": valuation_method,
        })
        sle.flags.ignore_permissions = True
        sle.submit()

    # Cancellation

    def cancel_ledger_entries(self):
        sle_names = frappe.get_all(
            "Stock Ledger Entry",
            filters={"voucher_type": "Sales Invoice", "voucher_no": self.name},
            pluck="name",
        )
        for sle_name in sle_names:
            sle = frappe.get_doc("Stock Ledger Entry", sle_name)
            sle.flags.ignore_permissions = True
            sle.cancel()
