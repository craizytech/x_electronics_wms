"""
Tests all Stock Entry behaviour: validation, business logic,
moving average valuation, and cancellation.

Covers:
    Validation:       bad inputs rejected before any DB write
    Business logic:   correct SLEs created after submit
    Moving average:   valuation math is arithmetically correct
    Cancellation:     ledger entries are properly reversed
"""

import frappe
import unittest
from frappe.utils import today

from x_electronics_wms.x_electronics_warehouse_management_system.utils.test_helpers import (
    make_warehouse,
    make_item,
    make_receipt,
    make_consume,
    make_transfer,
    get_balance,
    get_valuation_rate,
    cancel_and_delete_stock_entry,
    delete_test_records,
)


# Validation Tests
# Verify that the validate() method rejects bad inputs before any DB write.
# None of these tests should produce a Stock Ledger Entry.

class TestReceiptValidation(unittest.TestCase):
    """
    Validation rules for Receipt type Stock Entries.

    A Receipt is goods arriving into the warehouse. Rules:
        - Must have t_warehouse (target)
        - Must NOT have s_warehouse (no source — goods come from outside)
        - Must have rate > 0 (needed for moving average valuation)
        - Must have qty > 0
        - Cannot target a group warehouse
        - Cannot have an empty items list
    """

    def setUp(self):
        self.item      = make_item("_Test Item RV")
        self.warehouse = make_warehouse("_Test WH RV")

    def tearDown(self):
        delete_test_records([
            ("Item",      "_Test Item RV"),
            ("Warehouse", "_Test WH RV"),
        ])

    def _draft_receipt(self, items):
        """Build a draft Receipt without inserting it."""
        return frappe.get_doc({
            "doctype":          "Stock Entry",
            "stock_entry_type": "Receipt",
            "posting_date":     today(),
            "items":            items,
        })

    def test_receipt_requires_target_warehouse(self):
        """A Receipt with no t_warehouse must be rejected at validation."""
        with self.assertRaises(frappe.ValidationError):
            self._draft_receipt([{
                "item": "_Test Item RV",
                "qty":  10,
                "rate": 100,
            }]).insert(ignore_permissions=True)

    def test_receipt_rejects_source_warehouse(self):
        """
        A Receipt must not have s_warehouse.
        Goods arriving from outside have no internal source warehouse.
        """
        wh2 = make_warehouse("_Test WH RV2")
        with self.assertRaises(frappe.ValidationError):
            self._draft_receipt([{
                "item":        "_Test Item RV",
                "qty":         10,
                "rate":        100,
                "s_warehouse": "_Test WH RV2",
                "t_warehouse": "_Test WH RV",
            }]).insert(ignore_permissions=True)
        delete_test_records([("Warehouse", "_Test WH RV2")])

    def test_receipt_requires_rate(self):
        """
        Rate is required for Receipt because it seeds the moving average.
        Without a rate, the system cannot compute valuation_rate for the SLE.
        """
        with self.assertRaises(frappe.ValidationError):
            self._draft_receipt([{
                "item":        "_Test Item RV",
                "qty":         10,
                "t_warehouse": "_Test WH RV",
            }]).insert(ignore_permissions=True)

    def test_receipt_rejects_zero_qty(self):
        """Zero qty is meaningless — no stock movement occurs."""
        with self.assertRaises(frappe.ValidationError):
            self._draft_receipt([{
                "item":        "_Test Item RV",
                "qty":         0,
                "rate":        100,
                "t_warehouse": "_Test WH RV",
            }]).insert(ignore_permissions=True)

    def test_receipt_rejects_negative_qty(self):
        """
        Negative qty is not a valid Receipt.
        A reversal must be done via cancellation, not a negative receipt.
        """
        with self.assertRaises(frappe.ValidationError):
            self._draft_receipt([{
                "item":        "_Test Item RV",
                "qty":         -5,
                "rate":        100,
                "t_warehouse": "_Test WH RV",
            }]).insert(ignore_permissions=True)

    def test_receipt_rejects_empty_items(self):
        """A Stock Entry with no item rows has no effect and must be rejected."""
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({
                "doctype":          "Stock Entry",
                "stock_entry_type": "Receipt",
                "posting_date":     today(),
                "items":            [],
            }).insert(ignore_permissions=True)

    def test_receipt_rejects_group_warehouse(self):
        """
        Group warehouses are structural containers, not storage locations.
        Stock can only enter leaf warehouses.
        """
        grp = make_warehouse("_Test WH RV Group", is_group=1)
        with self.assertRaises(frappe.ValidationError):
            self._draft_receipt([{
                "item":        "_Test Item RV",
                "qty":         10,
                "rate":        100,
                "t_warehouse": "_Test WH RV Group",
            }]).insert(ignore_permissions=True)
        delete_test_records([("Warehouse", "_Test WH RV Group")])


class TestConsumeValidation(unittest.TestCase):
    """
    Validation rules for Consume type Stock Entries.

    A Consume is goods leaving the warehouse. Rules:
        - Must have s_warehouse (source)
        - Must NOT have t_warehouse (no destination — goods leave the system)
    """

    def setUp(self):
        self.item      = make_item("_Test Item CV")
        self.warehouse = make_warehouse("_Test WH CV")

    def tearDown(self):
        delete_test_records([
            ("Item",      "_Test Item CV"),
            ("Warehouse", "_Test WH CV"),
        ])

    def test_consume_requires_source_warehouse(self):
        """A Consume with no s_warehouse must be rejected — where does stock come from?"""
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({
                "doctype":          "Stock Entry",
                "stock_entry_type": "Consume",
                "posting_date":     today(),
                "items": [{
                    "item": "_Test Item CV",
                    "qty":  10,
                }],
            }).insert(ignore_permissions=True)

    def test_consume_rejects_target_warehouse(self):
        """
        A Consume with a t_warehouse is actually a Transfer.
        The system must reject Consume entries that specify a destination.
        """
        wh2 = make_warehouse("_Test WH CV2")
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({
                "doctype":          "Stock Entry",
                "stock_entry_type": "Consume",
                "posting_date":     today(),
                "items": [{
                    "item":        "_Test Item CV",
                    "qty":         10,
                    "s_warehouse": "_Test WH CV",
                    "t_warehouse": "_Test WH CV2",
                }],
            }).insert(ignore_permissions=True)
        delete_test_records([("Warehouse", "_Test WH CV2")])


class TestTransferValidation(unittest.TestCase):
    """
    Validation rules for Transfer type Stock Entries.

    A Transfer moves stock between warehouses. Rules:
        - Must have both s_warehouse and t_warehouse
        - s_warehouse and t_warehouse must be different
    """

    def setUp(self):
        self.item = make_item("_Test Item TV")
        self.src  = make_warehouse("_Test WH TV Src")
        self.dst  = make_warehouse("_Test WH TV Dst")

    def tearDown(self):
        delete_test_records([
            ("Item",      "_Test Item TV"),
            ("Warehouse", "_Test WH TV Src"),
            ("Warehouse", "_Test WH TV Dst"),
        ])

    def test_transfer_requires_source_warehouse(self):
        """A Transfer with no s_warehouse is incomplete — origin unknown."""
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({
                "doctype":          "Stock Entry",
                "stock_entry_type": "Transfer",
                "posting_date":     today(),
                "items": [{
                    "item":        "_Test Item TV",
                    "qty":         10,
                    "t_warehouse": "_Test WH TV Dst",
                }],
            }).insert(ignore_permissions=True)

    def test_transfer_requires_target_warehouse(self):
        """A Transfer with no t_warehouse is incomplete — destination unknown."""
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({
                "doctype":          "Stock Entry",
                "stock_entry_type": "Transfer",
                "posting_date":     today(),
                "items": [{
                    "item":        "_Test Item TV",
                    "qty":         10,
                    "s_warehouse": "_Test WH TV Src",
                }],
            }).insert(ignore_permissions=True)

    def test_transfer_rejects_same_warehouse(self):
        """
        Transferring from a warehouse to itself is a no-op.
        This is almost always a user error and must be rejected.
        """
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({
                "doctype":          "Stock Entry",
                "stock_entry_type": "Transfer",
                "posting_date":     today(),
                "items": [{
                    "item":        "_Test Item TV",
                    "qty":         10,
                    "s_warehouse": "_Test WH TV Src",
                    "t_warehouse": "_Test WH TV Src",
                }],
            }).insert(ignore_permissions=True)


# Business Logic Tests
# Verify that submitting a Stock Entry creates the correct ledger entries.

class TestReceiptLogic(unittest.TestCase):
    """
    After a valid Receipt is submitted, the system must:
        - Create exactly one Stock Ledger Entry
        - Record a positive qty_change
        - Increase the warehouse balance by the received qty
        - Record the correct stock value (qty * rate)
    """

    def setUp(self):
        self.item      = make_item("_Test Item RL")
        self.warehouse = make_warehouse("_Test WH RL")
        self.entries   = []

    def tearDown(self):
        for doc in self.entries:
            cancel_and_delete_stock_entry(doc)
        delete_test_records([
            ("Item",      "_Test Item RL"),
            ("Warehouse", "_Test WH RL"),
        ])

    def test_receipt_creates_exactly_one_sle(self):
        """
        A single-row Receipt must create exactly one SLE.
        One row = one warehouse leg = one ledger entry.
        """
        doc = make_receipt("_Test Item RL", "_Test WH RL", 10, 100)
        self.entries.append(doc)
        count = frappe.db.count("Stock Ledger Entry", {
            "voucher_no": doc.name, "docstatus": 1,
        })
        self.assertEqual(count, 1)

    def test_receipt_sle_qty_change_is_positive(self):
        """Incoming stock must record a positive qty_change in the ledger."""
        doc = make_receipt("_Test Item RL", "_Test WH RL", 10, 100)
        self.entries.append(doc)
        qty_change = frappe.db.get_value(
            "Stock Ledger Entry",
            {"voucher_no": doc.name, "docstatus": 1},
            "qty_change"
        )
        self.assertGreater(qty_change, 0)

    def test_receipt_increases_balance_by_received_qty(self):
        """Balance qty must increase by exactly the quantity received."""
        qty_before, _ = get_balance("_Test Item RL", "_Test WH RL")
        doc = make_receipt("_Test Item RL", "_Test WH RL", 25, 100)
        self.entries.append(doc)
        qty_after, _ = get_balance("_Test Item RL", "_Test WH RL")
        self.assertAlmostEqual(qty_after - qty_before, 25, places=2)

    def test_receipt_records_correct_stock_value(self):
        """
        Stock value for a first receipt must equal qty * rate.
        With no prior stock, there is no averaging — it is a direct product.
        """
        item = make_item("_Test Item RL2")
        wh   = make_warehouse("_Test WH RL2")
        doc  = make_receipt("_Test Item RL2", "_Test WH RL2", 10, 300)
        self.entries.append(doc)
        _, value = get_balance("_Test Item RL2", "_Test WH RL2")
        self.assertAlmostEqual(value, 3000.0, places=2)
        delete_test_records([("Item", "_Test Item RL2"), ("Warehouse", "_Test WH RL2")])


class TestConsumeLogic(unittest.TestCase):
    """
    After a valid Consume is submitted, the system must:
        - Create exactly one SLE with a negative qty_change
        - Decrease the warehouse balance by the consumed qty
    """

    def setUp(self):
        self.item      = make_item("_Test Item CL")
        self.warehouse = make_warehouse("_Test WH CL")
        self.entries   = []
        self.entries.append(make_receipt("_Test Item CL", "_Test WH CL", 100, 50))

    def tearDown(self):
        for doc in self.entries:
            cancel_and_delete_stock_entry(doc)
        delete_test_records([
            ("Item",      "_Test Item CL"),
            ("Warehouse", "_Test WH CL"),
        ])

    def test_consume_creates_exactly_one_sle(self):
        """A single-row Consume must create exactly one SLE."""
        doc = make_consume("_Test Item CL", "_Test WH CL", 10)
        self.entries.append(doc)
        count = frappe.db.count("Stock Ledger Entry", {
            "voucher_no": doc.name, "docstatus": 1,
        })
        self.assertEqual(count, 1)

    def test_consume_sle_qty_change_is_negative(self):
        """Outgoing stock must record a negative qty_change in the ledger."""
        doc = make_consume("_Test Item CL", "_Test WH CL", 10)
        self.entries.append(doc)
        qty_change = frappe.db.get_value(
            "Stock Ledger Entry",
            {"voucher_no": doc.name, "docstatus": 1},
            "qty_change"
        )
        self.assertLess(qty_change, 0)

    def test_consume_decreases_balance_by_consumed_qty(self):
        """Balance qty must decrease by exactly the quantity consumed."""
        qty_before, _ = get_balance("_Test Item CL", "_Test WH CL")
        doc = make_consume("_Test Item CL", "_Test WH CL", 30)
        self.entries.append(doc)
        qty_after, _ = get_balance("_Test Item CL", "_Test WH CL")
        self.assertAlmostEqual(qty_before - qty_after, 30, places=2)


class TestTransferLogic(unittest.TestCase):
    """
    After a valid Transfer is submitted, the system must:
        - Create exactly two SLEs (one out from source, one in to destination)
        - Decrease source balance by transferred qty
        - Increase destination balance by transferred qty
        - Keep total stock across both warehouses unchanged
        - Preserve valuation rate (no value created or destroyed)
    """

    def setUp(self):
        self.item    = make_item("_Test Item TL")
        self.src     = make_warehouse("_Test WH TL Src")
        self.dst     = make_warehouse("_Test WH TL Dst")
        self.entries = []
        self.entries.append(make_receipt("_Test Item TL", "_Test WH TL Src", 80, 75))

    def tearDown(self):
        for doc in self.entries:
            cancel_and_delete_stock_entry(doc)
        delete_test_records([
            ("Item",      "_Test Item TL"),
            ("Warehouse", "_Test WH TL Src"),
            ("Warehouse", "_Test WH TL Dst"),
        ])

    def test_transfer_creates_exactly_two_sles(self):
        """
        A Transfer must create two SLEs:
            1. Negative qty_change at source (stock leaves)
            2. Positive qty_change at destination (stock arrives)
        """
        doc = make_transfer("_Test Item TL", "_Test WH TL Src", "_Test WH TL Dst", 20)
        self.entries.append(doc)
        count = frappe.db.count("Stock Ledger Entry", {
            "voucher_no": doc.name, "docstatus": 1,
        })
        self.assertEqual(count, 2)

    def test_transfer_decreases_source_balance(self):
        """Source warehouse balance must decrease by the transferred qty."""
        src_before, _ = get_balance("_Test Item TL", "_Test WH TL Src")
        doc = make_transfer("_Test Item TL", "_Test WH TL Src", "_Test WH TL Dst", 20)
        self.entries.append(doc)
        src_after, _ = get_balance("_Test Item TL", "_Test WH TL Src")
        self.assertAlmostEqual(src_before - src_after, 20, places=2)

    def test_transfer_increases_destination_balance(self):
        """Destination warehouse balance must increase by the transferred qty."""
        dst_before, _ = get_balance("_Test Item TL", "_Test WH TL Dst")
        doc = make_transfer("_Test Item TL", "_Test WH TL Src", "_Test WH TL Dst", 20)
        self.entries.append(doc)
        dst_after, _ = get_balance("_Test Item TL", "_Test WH TL Dst")
        self.assertAlmostEqual(dst_after - dst_before, 20, places=2)

    def test_transfer_preserves_total_stock(self):
        """
        Total stock across source + destination must be identical before and after.
        A Transfer moves value — it must not create or destroy stock.
        """
        src_before, _ = get_balance("_Test Item TL", "_Test WH TL Src")
        dst_before, _ = get_balance("_Test Item TL", "_Test WH TL Dst")

        doc = make_transfer("_Test Item TL", "_Test WH TL Src", "_Test WH TL Dst", 30)
        self.entries.append(doc)

        src_after, _ = get_balance("_Test Item TL", "_Test WH TL Src")
        dst_after, _ = get_balance("_Test Item TL", "_Test WH TL Dst")

        self.assertAlmostEqual(
            src_before + dst_before,
            src_after  + dst_after,
            places=2
        )

    def test_transfer_preserves_valuation_rate(self):
        """
        The valuation rate at the destination must equal the source rate.
        A Transfer carries cost — it must not revalue the stock.
        """
        doc = make_transfer("_Test Item TL", "_Test WH TL Src", "_Test WH TL Dst", 10)
        self.entries.append(doc)
        src_rate = get_valuation_rate("_Test Item TL", "_Test WH TL Src")
        dst_rate = get_valuation_rate("_Test Item TL", "_Test WH TL Dst")
        self.assertAlmostEqual(src_rate, dst_rate, places=2)


# Moving Average Valuation

class TestMovingAverage(unittest.TestCase):
    """
    Verifies the moving average valuation formula across multiple scenarios.

    Formula:
        new_rate = (existing_qty * existing_rate + incoming_qty * incoming_rate)
                   / (existing_qty + incoming_qty)

    Known test case:
        Batch 1:  10 @ 100  →  rate = 100.00
        Batch 2:  10 @ 200  →  rate = (10*100 + 10*200) / 20      = 150.00
        Batch 3:   5 @ 120  →  rate = (20*150 +  5*120) / 25      = 144.00
        Consume 5           →  rate = 144.00  (unchanged — outgoing does not affect rate)

    Accounting identity that must always hold:
        stock_value = balance_qty * valuation_rate
    """

    def setUp(self):
        self.item      = make_item("_Test Item MA")
        self.warehouse = make_warehouse("_Test WH MA")
        self.entries   = []

    def tearDown(self):
        for doc in self.entries:
            cancel_and_delete_stock_entry(doc)
        delete_test_records([
            ("Item",      "_Test Item MA"),
            ("Warehouse", "_Test WH MA"),
        ])

    def test_first_receipt_rate_equals_purchase_rate(self):
        """
        With no prior stock, the moving average rate must equal the purchase rate.
        There is nothing to average — the first batch sets the rate directly.
        """
        doc = make_receipt("_Test Item MA", "_Test WH MA", 10, 100)
        self.entries.append(doc)
        self.assertAlmostEqual(
            get_valuation_rate("_Test Item MA", "_Test WH MA"), 100.0, places=2
        )

    def test_two_batches_moving_average(self):
        """
        Batch 1: 10 @ 100, Batch 2: 10 @ 200
        Expected: (10*100 + 10*200) / 20 = 150.00
        """
        self.entries.extend([
            make_receipt("_Test Item MA", "_Test WH MA", 10, 100),
            make_receipt("_Test Item MA", "_Test WH MA", 10, 200),
        ])
        self.assertAlmostEqual(
            get_valuation_rate("_Test Item MA", "_Test WH MA"), 150.0, places=2
        )

    def test_three_batches_moving_average(self):
        """
        Batch 1: 10 @ 100 → 100.00
        Batch 2: 10 @ 200 → 150.00
        Batch 3:  5 @ 120 → (20*150 + 5*120) / 25 = 144.00
        """
        self.entries.extend([
            make_receipt("_Test Item MA", "_Test WH MA", 10, 100),
            make_receipt("_Test Item MA", "_Test WH MA", 10, 200),
            make_receipt("_Test Item MA", "_Test WH MA",  5, 120),
        ])
        self.assertAlmostEqual(
            get_valuation_rate("_Test Item MA", "_Test WH MA"), 144.0, places=1
        )

    def test_consume_does_not_change_valuation_rate(self):
        """
        Consuming stock reduces qty but must NOT change the valuation rate.
        The moving average only changes when new stock arrives at a different price.
        Outgoing stock always uses the current rate — it never resets it.
        """
        self.entries.append(make_receipt("_Test Item MA", "_Test WH MA", 20, 150))
        rate_before = get_valuation_rate("_Test Item MA", "_Test WH MA")

        self.entries.append(make_consume("_Test Item MA", "_Test WH MA", 5))
        rate_after = get_valuation_rate("_Test Item MA", "_Test WH MA")

        self.assertAlmostEqual(rate_before, rate_after, places=2)

    def test_accounting_identity_holds(self):
        """
        At all times: stock_value = balance_qty * valuation_rate
        This is the fundamental accounting identity for inventory valuation.
        If this breaks, the ledger is internally inconsistent.
        """
        self.entries.extend([
            make_receipt("_Test Item MA", "_Test WH MA", 10, 100),
            make_receipt("_Test Item MA", "_Test WH MA", 10, 200),
        ])
        qty, value = get_balance("_Test Item MA", "_Test WH MA")
        rate = get_valuation_rate("_Test Item MA", "_Test WH MA")
        self.assertAlmostEqual(value, qty * rate, places=2)


# Cancellation

class TestCancellation(unittest.TestCase):
    """
    Cancelling a Stock Entry must fully reverse its effect on the ledger.

    After cancellation:
        - All related SLEs must have docstatus=2 (cancelled)
        - No active SLEs (docstatus=1) must remain for that voucher
        - Balance qty must return to its pre-entry value
        - Balance value must return to its pre-entry value
    """

    def setUp(self):
        self.item      = make_item("_Test Item CA")
        self.warehouse = make_warehouse("_Test WH CA")
        self.entries   = []

    def tearDown(self):
        for doc in self.entries:
            cancel_and_delete_stock_entry(doc)
        delete_test_records([
            ("Item",      "_Test Item CA"),
            ("Warehouse", "_Test WH CA"),
        ])

    def test_cancel_sets_sle_docstatus_to_cancelled(self):
        """All SLEs linked to a cancelled entry must have docstatus=2."""
        doc = make_receipt("_Test Item CA", "_Test WH CA", 40, 100)
        self.entries.append(doc)
        doc.reload()
        doc.cancel()
        cancelled = frappe.db.count("Stock Ledger Entry", {
            "voucher_no": doc.name, "docstatus": 2,
        })
        self.assertEqual(cancelled, 1)

    def test_cancel_leaves_no_active_sles(self):
        """After cancellation, zero active SLEs must remain for the voucher."""
        doc = make_receipt("_Test Item CA", "_Test WH CA", 10, 50)
        self.entries.append(doc)
        doc.reload()
        doc.cancel()
        active = frappe.db.count("Stock Ledger Entry", {
            "voucher_no": doc.name, "docstatus": 1,
        })
        self.assertEqual(active, 0)

    def test_cancel_restores_qty_balance(self):
        """
        Balance qty after cancellation must equal balance qty before submission.
        The entry must leave no net effect on the ledger.
        """
        qty_before, _ = get_balance("_Test Item CA", "_Test WH CA")
        doc = make_receipt("_Test Item CA", "_Test WH CA", 40, 100)
        self.entries.append(doc)
        doc.reload()
        doc.cancel()
        qty_after, _ = get_balance("_Test Item CA", "_Test WH CA")
        self.assertAlmostEqual(qty_after, qty_before, places=2)

    def test_cancel_restores_value_balance(self):
        """
        Stock value after cancellation must equal stock value before submission.
        No value must be created or destroyed by a cancelled entry.
        """
        _, value_before = get_balance("_Test Item CA", "_Test WH CA")
        doc = make_receipt("_Test Item CA", "_Test WH CA", 10, 500)
        self.entries.append(doc)
        doc.reload()
        doc.cancel()
        _, value_after = get_balance("_Test Item CA", "_Test WH CA")
        self.assertAlmostEqual(value_after, value_before, places=2)
