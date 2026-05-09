# Copyright (c) 2026, Eammon Kiprotich and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import nowtime

class StockEntry(Document):
    # begin: auto-generated types
    # This code is auto-generated. Do not modify anything in this block.

    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from frappe.types import DF
        from x_electronics_wms.x_electronics_warehouse_management_system.doctype.stock_entry_detail.stock_entry_detail import StockEntryDetail

        amended_from: DF.Link | None
        items: DF.Table[StockEntryDetail]
        posting_date: DF.Date
        posting_time: DF.Time | None
        stock_entry_type: DF.Literal["Receipt", "Consume", "Transfer"]
    # end: auto-generated types

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
            # Get the items valuation method from the Items doctype
            valuation_method = frappe.db.get_value(
                "Item", row.item, "valuation_method"
            ) or "Moving Average"

            # Incoming ledger (Receipt / Transfer in)
            if row.t_warehouse:
                incoming_rate = self._get_incoming_rate(row, valuation_method)
                self._create_sle(
                    item=row.item,
                    warehouse=row.t_warehouse,
                    qty_change=row.qty,
                    incoming_rate=incoming_rate,
                    posting_dt=posting_dt,
                    valuation_method=valuation_method
                )

            # Outgoing leg (Consume / Transfer out)
            if row.s_warehouse:
                outgoing_rate = self._get_outgoing_rate(row.item, row.s_warehouse, row.qty, valuation_method)
                self._create_sle(
                    item=row.item,
                    warehouse=row.s_warehouse,
                    qty_change=-row.qty,
                    incoming_rate=outgoing_rate,
                    posting_dt=posting_dt,
                    valuation_method=valuation_method
                )

    def _get_incoming_rate(self, row, valuation_method):
        """
        For Receipt: use the rate entered by the user.
        For Transfer: use the current valuation rate of the source warehouse.
        """
        if self.stock_entry_type == "Receipt":
            return row.rate

        if self.stock_entry_type == "Transfer":
            return self._get_outgoing_rate(row.item, row.s_warehouse, row.qty, valuation_method)

        return 0.0

    def _get_outgoing_rate(self, item, warehouse, qty, valuation_method):
        """
        Route to the correct outgoing rate calculation based on valuation method.

        Moving Average: rate = total_value / total_qty
        FIFO:           rate = Consume Oldest
        LIFO:           rate = Consume Newest
        """

        if valuation_method == "Moving Average":
            return self._moving_average_rate(item, warehouse)
        elif valuation_method == "FIFO":
            return self._fifo_rate(item, warehouse, qty)
        elif valuation_method == "LIFO":
            return self._lifo_rate(item, warehouse, qty)
        else:
            return self._moving_average_rate(item, warehouse)

    # Moving Average

    def _moving_average_rate(self, item, warehouse):
        """
        Stateless moving average: total_value / total_qty.
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
    
    def _fifo_rate(self, item, warehouse, qty_to_consume):
        """
        Consume the oldest receipts first.

        The ledger contains every receipt as a +ve qty change with its
        original rate stored as valuation rate.

        1. We fetch the all the receipts SLESs in chronological order (oldest first).
        2. Fetch all the Consume and Transfer SLEs in chronological order
        3. Walking through receipts oldest-first, subtracting consumptions to find
            what qty is still available
        4. Consume qty_to_consume from the oldest stock available.

        This is stateless though computationaly expensive.
        
        """

        # Step 1: Get all incoming stocks (receipts and transfer-ins) oldest first
        incoming_layers = frappe.db.sql("""
            SELECT
                name,
                posting_datetime,
                qty_change       AS qty,
                valuation_rate   AS rate
            FROM `tabStock Ledger Entry`
            WHERE
                item      = %(item)s
                AND warehouse = %(warehouse)s
                AND qty_change > 0
                AND docstatus = 1
            ORDER BY posting_datetime ASC, creation ASC
        """, {"item": item, "warehouse": warehouse}, as_dict=True)

        # Step 2: Get total already consumed (all outgoing movements)
        # We use the sum of negative qty_changes to know how much has left this warehouse
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

        # Step 3: Walk through stock oldest-first, subtract already-consumed qty,
        # then consume qty_to_consume from the remaining layers.
        return self._consume_layers_in_order(
            incoming_layers, consumed_so_far, qty_to_consume
        )
    
    # LIFO: Last in First Out
    def _lifo_rate(self, item, warehouse, qty_to_consume):
        """
        LIFO: consume latest receipts layers first.
        Same as FIFO except that the newest stock items are consumed first.
        """
        # Step 1: Get incoming stock newest first
        incoming_layers = frappe.db.sql("""
            SELECT
                name,
                posting_datetime,
                qty_change       AS qty,
                valuation_rate   AS rate
            FROM `tabStock Ledger Entry`
            WHERE
                item      = %(item)s
                AND warehouse = %(warehouse)s
                AND qty_change > 0
                AND docstatus = 1
            ORDER BY posting_datetime DESC, creation DESC
        """, {"item": item, "warehouse": warehouse}, as_dict=True)

        # Step 2: Get total consumed by summing Transfers and consumes
        already_consumed = frappe.db.sql("""
            SELECT COALESCE(SUM(qty_change), 0) AS total_out
            FROM `tabStock Ledger Entry`
            WHERE
                item      = %(item)s
                AND warehouse = %(warehouse)s
                AND qty_change < 0
                AND docstatus = 1
        """, {"item": item, "warehouse": warehouse}, as_dict=True)

        # For LIFO, "already consumed" means we've already consumed from the newest layers.
        # we have to subtract these first before finding what is still available
        consumed_so_far = abs(already_consumed[0].total_out or 0.0)

        return self._consume_layers_in_order(
            incoming_layers, consumed_so_far, qty_to_consume
        )
    
    # Shared layer consumption logic for both fifo and lifo

    def _consume_layers_in_order(self, layers, already_consumed, qty_to_consume):
        """
        Given a list of stock layers (in the order they should be consumed),
        subtract `already_consumed` quantity first (stock that has already
        left in previous transactions), then consume `qty_to_consume` from
        what remains, and return the blended weighted average rate.

        This is the core of the FIFO/LIFO stateless reconstruction algorithm.

        Args:
            layers:           list of dicts with keys: qty (float), rate (float)
                              ordered in the consumption direction (oldest-first
                              for FIFO, newest-first for LIFO)
            already_consumed: how much has already been consumed from this
                              warehouse in previous transactions
            qty_to_consume:   how much we are consuming right now

        Returns:
            float: blended weighted average rate for qty_to_consume
        """
        remaining_to_subtract = already_consumed
        remaining_to_consume = qty_to_consume

        total_value_consumed = 0.0
        total_qty_consumed = 0.0

        for layer in layers:
            layer_qty = float(layer.qty or 0)
            layer_rate = float(layer.rate or 0)

            if layer_qty <= 0:
                continue

            # Subtract already consumed stock from this layer
            if remaining_to_subtract > 0:
                subtracted = min(layer_qty, remaining_to_subtract)
                layer_qty -= subtracted
                remaining_to_subtract -= subtracted

            # Then consume from what remains in this layer
            if layer_qty > 0 and remaining_to_consume > 0:
                consumable = min(layer_qty, remaining_to_consume)
                total_value_consumed += consumable * layer_rate
                total_qty_consumed += consumable
                remaining_to_consume -= consumable
            
            if remaining_to_consume <= 0:
                break

        # return the blended rate if we consumed sth
        if total_qty_consumed > 0:
            return total_value_consumed / total_qty_consumed
        
        # if layers were exhausted i.e in a -ve stock situation
        # we fall bacl to moving avg to avoid division by 0
        return self._moving_average_rate(
            layers[0].get("item") if layers else "",
            layers[0].get("warehouse") if layers else ""
        ) if layers else 0.0
    
    # Shared SLE Creation method

    def _create_sle(self, item, warehouse, qty_change, incoming_rate,
                    posting_dt, valuation_method):
        """
        Write one immutable Stock Ledger Entry.

        valuation_rate stored on the SLE means different things per method:

        Moving Average:
            valuation_rate = new blended moving average after this receipt.

        FIFO / LIFO:
            valuation_rate on INCOMING SLE = the actual purchase rate of
            this specific batch. The layer reconstruction algorithm reads
            this field back to know "what did this batch cost?". If we
            stored the blended average instead, the layer algorithm would
            see the same rate on every layer and FIFO/LIFO would produce
            identical results to Moving Average — which is wrong.

            valuation_rate on OUTGOING SLE = the blended rate computed by
            _fifo_rate or _lifo_rate (already passed in as incoming_rate).
        """
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

        current_qty   = float(current.qty   or 0.0)
        current_value = float(current.value or 0.0)

        if qty_change > 0:
            # Incoming stock
            stock_value_change = qty_change * incoming_rate

            if valuation_method == "Moving Average":
                # Store the new blended average — this is what future
                # _moving_average_rate queries will derive from SUM(stock_value)
                new_qty        = current_qty + qty_change
                new_value      = current_value + stock_value_change
                valuation_rate = new_value / new_qty if new_qty else incoming_rate
            else:
                # FIFO / LIFO: store the raw purchase rate of THIS batch.
                # The layer reconstruction algorithm reads valuation_rate
                # back from each incoming SLE to know the batch's unit cost.
                # Blending it here would destroy that information.
                valuation_rate = incoming_rate

        else:
            # Outgoing stock — incoming_rate is the rate already computed
            # by _moving_average_rate, _fifo_rate, or _lifo_rate.
            valuation_rate     = incoming_rate
            stock_value_change = qty_change * incoming_rate  # negative

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
            "valuation_method": valuation_method,
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
