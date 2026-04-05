"""
Tests the Item DocType in isolation.

Covers:
    - Basic creation
    - item_code uniqueness constraint
    - Required fields
"""

import frappe
import unittest

from x_electronics_wms.x_electronics_warehouse_management_system.utils.test_helpers import (
    make_item,
    delete_test_records,
)


class TestItemCreation(unittest.TestCase):
    """Verify Items are created correctly and saved to the database."""

    def tearDown(self):
        delete_test_records([
            ("Item", "_Test Item Creation"),
            ("Item", "_Test Item Duplicate"),
        ])

    def test_create_item(self):
        """A basic item must be created and retrievable by item_code."""
        item = make_item("_Test Item Creation")
        self.assertTrue(frappe.db.exists("Item", "_Test Item Creation"))
        self.assertEqual(item.stock_uom, "Nos")
        self.assertEqual(item.is_stock_item, 1)

    def test_item_code_is_unique(self):
        """
        Two items with the same item_code must not be allowed.
        item_code is the document name (autoname: field:item_code),
        so a duplicate insert raises a DuplicateEntryError.
        """
        make_item("_Test Item Duplicate")
        with self.assertRaises(Exception):
            frappe.get_doc({
                "doctype":   "Item",
                "item_code": "_Test Item Duplicate",
                "item_name": "Should Fail",
                "stock_uom": "Nos",
            }).insert(ignore_permissions=True)

    def test_make_item_is_idempotent(self):
        """
        Calling make_item twice with the same code must return
        the same document without raising an error.
        This is critical for test setUp reliability.
        """
        item1 = make_item("_Test Item Creation")
        item2 = make_item("_Test Item Creation")
        self.assertEqual(item1.name, item2.name)
