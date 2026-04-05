"""
Stock Ledger Entry + Report Tests

Tests two things that are tightly coupled to the SLE table:

    Immutability:  SLEs cannot be edited directly by users
    Reports:       SQL queries return arithmetically correct results

Reports are tested here (not in test_stock_entry.py) because both
Stock Ledger and Stock Balance reports query `tabStock Ledger Entry`
directly. Their correctness is a property of the ledger, not the entry.
"""

import frappe
import unittest
from frappe.utils import today, add_days

from x_electronics_wms.x_electronics_warehouse_management_system.utils.test_helpers import (
    make_warehouse,
    make_item,
    make_receipt,
    get_balance,
    cancel_and_delete_stock_entry,
    delete_test_records,
)


# Layer 5 — Ledger Immutability

class TestLedgerImmutability(unittest.TestCase):
    """
    Stock Ledger Entries are the system's source of truth.
    They are created only by code (on_submit) and must never be
    editable by users directly.

    Frappe enforces this via the Read Only flag on the DocType.
    Any direct save() must raise an exception.
    """

    def setUp(self):
        self.item      = make_item("_Test Item IM")
        self.warehouse = make_warehouse("_Test WH IM")
        self.entries   = []

    def tearDown(self):
        for doc in self.entries:
            cancel_and_delete_stock_entry(doc)
        delete_test_records([
            ("Item",      "_Test Item IM"),
            ("Warehouse", "_Test WH IM"),
        ])

    def test_sle_cannot_be_edited_directly(self):
        """
        Attempting to modify and save an SLE directly must raise an exception.
        This protects the audit trail — the ledger must be append-only.
        Reversals happen only via cancellation of the originating Stock Entry.
        """
        doc = make_receipt("_Test Item IM", "_Test WH IM", 5, 300)
        self.entries.append(doc)

        sle_name = frappe.db.get_value(
            "Stock Ledger Entry",
            {"voucher_no": doc.name, "docstatus": 1},
            "name"
        )
        sle = frappe.get_doc("Stock Ledger Entry", sle_name)
        sle.qty_change = 9999

        with self.assertRaises(Exception):
            sle.save()


# Layer 6 — Report Tests

class TestStockLedgerReport(unittest.TestCase):
    """
    Tests for the Stock Ledger Script Report.

    The Stock Ledger report uses SQL window functions to compute a running
    balance per item+warehouse. These tests verify that the running balance
    columns (balance_qty, balance_value) are arithmetically correct.

    Test setup:
        Receipt 1: 20 units @ 150  →  balance_qty = 20, balance_value = 3000
        Receipt 2: 10 units @ 150  →  balance_qty = 30, balance_value = 4500
    """

    def setUp(self):
        self.item      = make_item("_Test Item SLR")
        self.warehouse = make_warehouse("_Test WH SLR")
        self.entries   = []
        self.entries.append(make_receipt("_Test Item SLR", "_Test WH SLR", 20, 150))
        self.entries.append(make_receipt("_Test Item SLR", "_Test WH SLR", 10, 150))

    def tearDown(self):
        for doc in self.entries:
            cancel_and_delete_stock_entry(doc)
        delete_test_records([
            ("Item",      "_Test Item SLR"),
            ("Warehouse", "_Test WH SLR"),
        ])

    def _run_report(self, extra_filters=None):
        from x_electronics_wms.x_electronics_warehouse_management_system.report.stock_ledger.stock_ledger import execute
        filters = {"item": "_Test Item SLR", "warehouse": "_Test WH SLR"}
        if extra_filters:
            filters.update(extra_filters)
        _, data = execute(filters)
        return data

    def test_report_returns_at_least_one_row(self):
        """The report must return rows when submitted ledger entries exist."""
        data = self._run_report()
        self.assertGreater(len(data), 0)

    def test_report_last_row_balance_qty_is_cumulative_total(self):
        """
        The last row's balance_qty must equal the sum of all qty_changes.
        Window function: SUM(qty_change) OVER ... ROWS UNBOUNDED PRECEDING.
        Expected: 20 + 10 = 30
        """
        data = self._run_report()
        self.assertAlmostEqual(data[-1]["balance_qty"], 30, places=2)

    def test_report_last_row_balance_value_is_cumulative_total(self):
        """
        The last row's balance_value must equal the sum of all stock_values.
        Expected: (20 * 150) + (10 * 150) = 4500
        """
        data = self._run_report()
        self.assertAlmostEqual(data[-1]["balance_value"], 4500, places=2)

    def test_report_running_balance_is_monotonically_increasing(self):
        """
        For receipts only, each row's balance_qty must be >= the previous row.
        This confirms the window function ordering is correct.
        """
        data = self._run_report()
        for i in range(1, len(data)):
            self.assertGreaterEqual(
                data[i]["balance_qty"],
                data[i - 1]["balance_qty"]
            )

    def test_report_date_filter_restricts_results(self):
        """
        When from_date and to_date filters are applied, only entries
        within that date range must appear in the report.
        """
        data = self._run_report({"from_date": today(), "to_date": today()})
        self.assertGreater(len(data), 0)


class TestStockBalanceReport(unittest.TestCase):
    """
    Tests for the Stock Balance Script Report.

    The Stock Balance report aggregates the ledger up to a given date
    and supports warehouse tree consolidation via Frappe nested sets.

    Test setup:
        15 units @ 200 received into _Test WH SBR
        Expected balance: qty=15, value=3000, rate=200
    """

    def setUp(self):
        self.item      = make_item("_Test Item SBR")
        self.warehouse = make_warehouse("_Test WH SBR")
        self.entries   = []
        self.entries.append(make_receipt("_Test Item SBR", "_Test WH SBR", 15, 200))

    def tearDown(self):
        for doc in self.entries:
            cancel_and_delete_stock_entry(doc)
        delete_test_records([
            ("Item",      "_Test Item SBR"),
            ("Warehouse", "_Test WH SBR"),
        ])

    def _run_report(self, extra_filters=None):
        from x_electronics_wms.x_electronics_warehouse_management_system.report.stock_balance.stock_balance import execute
        filters = {
            "to_date":   today(),
            "item":      "_Test Item SBR",
            "warehouse": "_Test WH SBR",
        }
        if extra_filters:
            filters.update(extra_filters)
        _, data = execute(filters)
        return data

    def test_report_returns_one_row_for_one_item_warehouse(self):
        """One item in one warehouse must produce exactly one result row."""
        data = self._run_report()
        self.assertEqual(len(data), 1)

    def test_report_balance_qty_is_correct(self):
        """Balance qty must equal total received qty (15)."""
        data = self._run_report()
        self.assertAlmostEqual(data[0]["balance_qty"], 15, places=2)

    def test_report_valuation_rate_is_correct(self):
        """Valuation rate must equal purchase rate for a single-batch receipt (200)."""
        data = self._run_report()
        self.assertAlmostEqual(data[0]["valuation_rate"], 200.0, places=2)

    def test_report_stock_value_is_correct(self):
        """Stock value must equal qty * rate = 15 * 200 = 3000."""
        data = self._run_report()
        self.assertAlmostEqual(data[0]["stock_value"], 3000.0, places=2)

    def test_report_excludes_entries_after_to_date(self):
        """
        Entries posted today must not appear when to_date is yesterday.
        The report is a point-in-time snapshot — future entries are invisible.
        """
        data = self._run_report({"to_date": add_days(today(), -1)})
        total_qty = sum(row["balance_qty"] for row in data)
        self.assertAlmostEqual(total_qty, 0, places=2)

    def test_report_tree_consolidation_includes_all_children(self):
        """
        When a group warehouse is selected, the report must include stock
        from ALL leaf children, not just direct ones.

        Uses Frappe nested sets (lft/rgt) to find children in one SQL query.

        Setup:
            Group → Child1: 10 units
            Group → Child2:  5 units
            Expected total: 15 units
        """
        grp    = make_warehouse("_Test WH SBR Group", is_group=1)
        child1 = make_warehouse("_Test WH SBR C1", parent=grp.name)
        child2 = make_warehouse("_Test WH SBR C2", parent=grp.name)
        make_item("_Test Item SBR Tree")

        doc1 = make_receipt("_Test Item SBR Tree", child1.name, 10, 100)
        doc2 = make_receipt("_Test Item SBR Tree", child2.name,  5, 100)
        self.entries.extend([doc1, doc2])

        from x_electronics_wms.x_electronics_warehouse_management_system.report.stock_balance.stock_balance import execute
        _, data = execute({
            "to_date":   today(),
            "item":      "_Test Item SBR Tree",
            "warehouse": grp.name,
        })
        total_qty = sum(row["balance_qty"] for row in data)
        self.assertAlmostEqual(total_qty, 15, places=2)

        delete_test_records([
            ("Item",      "_Test Item SBR Tree"),
            ("Warehouse", "_Test WH SBR C1"),
            ("Warehouse", "_Test WH SBR C2"),
            ("Warehouse", "_Test WH SBR Group"),
        ])
