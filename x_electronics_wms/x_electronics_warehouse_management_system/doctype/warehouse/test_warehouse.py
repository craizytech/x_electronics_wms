"""
Tests the Warehouse DocType in isolation.

Covers:
    - Creating leaf and group warehouses
    - Parent-child tree relationships
    - Blocking group warehouses from use in transactions

These tests do NOT test stock movement — that belongs in test_stock_entry.py.
"""

import frappe
import unittest

from x_electronics_wms.x_electronics_warehouse_management_system.utils.test_helpers import (
    make_warehouse,
    make_item,
    delete_test_records,
)


class TestWarehouseCreation(unittest.TestCase):
    """
    Verify that warehouses can be created with the correct structure.
    Frappe handles nested sets (lft/rgt) automatically on insert.
    """

    def tearDown(self):
        delete_test_records([
            ("Warehouse", "_Test WH Leaf"),
            ("Warehouse", "_Test WH Group"),
            ("Warehouse", "_Test WH Child"),
            ("Warehouse", "_Test WH Parent"),
        ])

    def test_create_leaf_warehouse(self):
        """A leaf warehouse (is_group=0) must be created and saved correctly."""
        wh = make_warehouse("_Test WH Leaf")
        self.assertTrue(frappe.db.exists("Warehouse", "_Test WH Leaf"))
        self.assertEqual(wh.is_group, 0)

    def test_create_group_warehouse(self):
        """A group warehouse (is_group=1) acts as a container for child warehouses."""
        wh = make_warehouse("_Test WH Group", is_group=1)
        self.assertTrue(frappe.db.exists("Warehouse", "_Test WH Group"))
        self.assertEqual(wh.is_group, 1)

    def test_parent_child_relationship(self):
        """
        A child warehouse must correctly reference its parent.
        Frappe's tree system uses parent_warehouse for the relationship
        and manages lft/rgt nested sets automatically.
        """
        parent = make_warehouse("_Test WH Parent", is_group=1)
        child  = make_warehouse("_Test WH Child", parent=parent.name, is_group=0)
        self.assertEqual(child.parent_warehouse, parent.name)

    def test_nested_sets_populated(self):
        """
        Frappe must populate lft and rgt on every warehouse.
        These are used by the Stock Balance report for tree traversal.
        Without them, get_leaf_warehouses() returns nothing.
        """
        wh = make_warehouse("_Test WH Leaf")
        lft = frappe.db.get_value("Warehouse", "_Test WH Leaf", "lft")
        rgt = frappe.db.get_value("Warehouse", "_Test WH Leaf", "rgt")
        self.assertIsNotNone(lft)
        self.assertIsNotNone(rgt)
        self.assertGreater(rgt, lft)


class TestGroupWarehouseRestriction(unittest.TestCase):
    """
    Group warehouses are structural containers only.
    They must be rejected when used as transaction warehouses.
    """

    def setUp(self):
        self.item  = make_item("_Test Item WH Restriction")
        self.group = make_warehouse("_Test WH Restriction Group", is_group=1)

    def tearDown(self):
        delete_test_records([
            ("Item",      "_Test Item WH Restriction"),
            ("Warehouse", "_Test WH Restriction Group"),
        ])

    def test_group_warehouse_rejected_in_receipt(self):
        """
        A Receipt targeting a group warehouse must raise ValidationError.
        Stock can only be received into leaf warehouses.
        """
        doc = frappe.get_doc({
            "doctype":          "Stock Entry",
            "stock_entry_type": "Receipt",
            "posting_date":     frappe.utils.today(),
            "items": [{
                "item":        "_Test Item WH Restriction",
                "qty":         10,
                "rate":        100,
                "t_warehouse": "_Test WH Restriction Group",
            }],
        })
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    def test_group_warehouse_rejected_in_consume(self):
        """
        A Consume from a group warehouse must raise ValidationError.
        Stock can only be consumed from leaf warehouses.
        """
        doc = frappe.get_doc({
            "doctype":          "Stock Entry",
            "stock_entry_type": "Consume",
            "posting_date":     frappe.utils.today(),
            "items": [{
                "item":        "_Test Item WH Restriction",
                "qty":         10,
                "s_warehouse": "_Test WH Restriction Group",
            }],
        })
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)
