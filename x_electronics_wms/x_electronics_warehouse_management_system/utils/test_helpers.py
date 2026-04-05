"""
Shared test methods

This module contains ONLY factory functions and helpers used across
multiple test files. It contains no test cases itself.
"""

import frappe
from frappe.utils import today


# Record factories
# These create the minimum viable record for each DocType.
# They are idempotent — safe to call in setUp even if record already exists.

def make_warehouse(name, parent=None, is_group=0):
    """
    Create a Warehouse and return its document.
    If the warehouse already exists, return the existing document unchanged.

    Args:
        name (str): Warehouse name. Must start with _Test in test contexts.
        parent (str|None): Name of parent warehouse for tree placement.
        is_group (int): 1 if this is a group (container) warehouse, 0 if leaf.

    Returns:
        Document: The Warehouse frappe document.
    """
    if frappe.db.exists("Warehouse", name):
        return frappe.get_doc("Warehouse", name)
    doc = frappe.get_doc({
        "doctype":          "Warehouse",
        "warehouse_name":   name,
        "parent_warehouse": parent,
        "is_group":         is_group,
    })
    doc.insert(ignore_permissions=True)
    return doc


def make_item(item_code):
    """
    Create an Item and return its document.
    If the item already exists, return the existing document unchanged.

    Args:
        item_code (str): Unique item code. Must start with _Test in test contexts.

    Returns:
        Document: The Item frappe document.
    """
    if frappe.db.exists("Item", item_code):
        return frappe.get_doc("Item", item_code)
    doc = frappe.get_doc({
        "doctype":       "Item",
        "item_code":     item_code,
        "item_name":     item_code,
        "stock_uom":     "Nos",
        "is_stock_item": 1,
    })
    doc.insert(ignore_permissions=True)
    return doc


def make_receipt(item, warehouse, qty, rate, posting_date=None):
    """
    Create and submit a Receipt Stock Entry (goods coming in).

    A Receipt requires:
        - t_warehouse (target) only — no source
        - rate > 0 for moving average valuation
        - qty > 0

    Args:
        item (str): Item code.
        warehouse (str): Target warehouse name.
        qty (float): Quantity received.
        rate (float): Purchase/incoming rate per unit.
        posting_date (str|None): Date string YYYY-MM-DD. Defaults to today.

    Returns:
        Document: The submitted Stock Entry document.
    """
    doc = frappe.get_doc({
        "doctype":          "Stock Entry",
        "stock_entry_type": "Receipt",
        "posting_date":     posting_date or today(),
        "posting_time":     "10:00:00",
        "items": [{
            "item":        item,
            "qty":         qty,
            "rate":        rate,
            "t_warehouse": warehouse,
        }],
    })
    doc.insert(ignore_permissions=True)
    doc.submit()
    return doc


def make_consume(item, warehouse, qty, posting_date=None):
    """
    Create and submit a Consume Stock Entry (goods going out).

    A Consume requires:
        - s_warehouse (source) only — no target
        - Stock must already exist in the source warehouse

    Args:
        item (str): Item code.
        warehouse (str): Source warehouse name.
        qty (float): Quantity consumed.
        posting_date (str|None): Date string YYYY-MM-DD. Defaults to today.

    Returns:
        Document: The submitted Stock Entry document.
    """
    doc = frappe.get_doc({
        "doctype":          "Stock Entry",
        "stock_entry_type": "Consume",
        "posting_date":     posting_date or today(),
        "posting_time":     "10:00:00",
        "items": [{
            "item":        item,
            "qty":         qty,
            "s_warehouse": warehouse,
        }],
    })
    doc.insert(ignore_permissions=True)
    doc.submit()
    return doc


def make_transfer(item, src, dst, qty, posting_date=None):
    """
    Create and submit a Transfer Stock Entry (goods moving between warehouses).

    A Transfer requires:
        - s_warehouse (source) and t_warehouse (target) — both required
        - src and dst must be different warehouses
        - Stock must already exist in src

    Args:
        item (str): Item code.
        src (str): Source warehouse name.
        dst (str): Destination warehouse name.
        qty (float): Quantity to transfer.
        posting_date (str|None): Date string YYYY-MM-DD. Defaults to today.

    Returns:
        Document: The submitted Stock Entry document.
    """
    doc = frappe.get_doc({
        "doctype":          "Stock Entry",
        "stock_entry_type": "Transfer",
        "posting_date":     posting_date or today(),
        "posting_time":     "10:00:00",
        "items": [{
            "item":        item,
            "qty":         qty,
            "s_warehouse": src,
            "t_warehouse": dst,
        }],
    })
    doc.insert(ignore_permissions=True)
    doc.submit()
    return doc


# Balance query helpers
# These mirror the stateless ledger design — balances are always derived,
# never stored. These helpers are the test-side equivalent of that pattern.

def get_balance(item, warehouse):
    """
    Return the current stock balance for an item in a warehouse.
    Derived from live ledger — same logic the system uses internally.

    Only counts submitted (docstatus=1) entries.
    Cancelled entries (docstatus=2) are excluded automatically.

    Args:
        item (str): Item code.
        warehouse (str): Warehouse name.

    Returns:
        tuple: (qty: float, value: float)
            qty   — net quantity currently in stock
            value — net stock value at moving average cost
    """
    result = frappe.db.sql("""
        SELECT
            COALESCE(SUM(qty_change), 0)  AS qty,
            COALESCE(SUM(stock_value), 0) AS value
        FROM `tabStock Ledger Entry`
        WHERE
            item      = %(item)s
            AND warehouse = %(warehouse)s
            AND docstatus = 1
    """, {"item": item, "warehouse": warehouse}, as_dict=True)
    row = result[0]
    return float(row.qty), float(row.value)


def get_valuation_rate(item, warehouse):
    """
    Return the current moving average valuation rate for an item in a warehouse.
    Returns 0.0 if there is no stock (avoids division by zero).

    Formula: total_stock_value / total_qty

    Args:
        item (str): Item code.
        warehouse (str): Warehouse name.

    Returns:
        float: Valuation rate per unit. 0.0 if no stock exists.
    """
    qty, value = get_balance(item, warehouse)
    return (value / qty) if qty > 0 else 0.0


# Cleanup helpers
# Used in tearDown to ensure no test data remains in the DB after each test.
# All helpers are safe to call even if the record no longer exists.

def cancel_and_delete_stock_entry(doc):
    """
    Safely cancel and delete a Stock Entry and all its linked ledger entries.

    Handles all docstatus states:
        - docstatus=0 (draft): just delete
        - docstatus=1 (submitted): cancel first, then delete
        - docstatus=2 (already cancelled): just delete

    Linked SLEs are cancelled via the on_cancel hook automatically.

    Args:
        doc: A Stock Entry frappe document (submitted or draft).
    """
    try:
        doc.reload()
        if doc.docstatus == 1:
            doc.cancel()
        frappe.delete_doc(
            "Stock Entry", doc.name,
            force=True,
            ignore_permissions=True
        )
    except Exception:
        # If doc was already deleted by another cleanup path, continue silently
        pass


def delete_test_records(doctype_name_pairs):
    """
    Delete a list of test records by (doctype, name) pairs.
    Safe to call even if records do not exist.

    Call this at the end of tearDown for Items, Warehouses, and any
    other master data created during the test.

    IMPORTANT: Delete in reverse dependency order.
    Example — delete Stock Entry before Item before Warehouse.

    Args:
        doctype_name_pairs (list): List of (doctype: str, name: str) tuples.

    Example:
        delete_test_records([
            ("Item",      "_Test Item ABC"),
            ("Warehouse", "_Test WH ABC"),
        ])
    """
    for doctype, name in doctype_name_pairs:
        try:
            if frappe.db.exists(doctype, name):
                frappe.delete_doc(
                    doctype, name,
                    force=True,
                    ignore_permissions=True
                )
        except Exception:
            pass
