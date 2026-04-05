# Copyright (c) 2026, Eammon Kiprotich and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters: dict | None = None):
	"""Return columns and data for the report.

	This is the main entry point for the report. It accepts the filters as a
	dictionary and should return columns and data. It is called by the framework
	every time the report is refreshed or a filter is updated.
	"""
	filters = filters or {}
	columns = get_columns()
	data = get_data(filters)

	return columns, data


def get_columns() -> list[dict]:
	"""Return columns for the report.

	One field definition per column, just like a DocType field definition.
	"""
	return [
        {
            "label":     _("Item"),
            "fieldname": "item",
            "fieldtype": "Link",
            "options":   "Item",
            "width":     140,
        },
        {
            "label":     _("Item Name"),
            "fieldname": "item_name",
            "fieldtype": "Data",
            "width":     200,
        },
        {
            "label":     _("Warehouse"),
            "fieldname": "warehouse",
            "fieldtype": "Link",
            "options":   "Warehouse",
            "width":     180,
        },
        {
            "label":     _("Balance Qty"),
            "fieldname": "balance_qty",
            "fieldtype": "Float",
            "width":     120,
        },
        {
            "label":     _("Valuation Rate"),
            "fieldname": "valuation_rate",
            "fieldtype": "Currency",
            "width":     140,
        },
        {
            "label":     _("Stock Value"),
            "fieldname": "stock_value",
            "fieldtype": "Currency",
            "width":     140,
        },
    ]


def get_data(filters):
    to_date   = filters.get("to_date") or frappe.utils.today()
    item      = filters.get("item")
    warehouse = filters.get("warehouse")

    values = {"to_date": to_date}
    warehouse_condition = ""
    item_condition = ""

    if warehouse:
        leaf_warehouses = get_leaf_warehouses(warehouse)
        if not leaf_warehouses:
            return []
        placeholders = ", ".join([f"%(wh{i})s" for i in range(len(leaf_warehouses))])
        for i, wh in enumerate(leaf_warehouses):
            values[f"wh{i}"] = wh
        warehouse_condition = f"AND sle.warehouse IN ({placeholders})"

    if item:
        item_condition = "AND sle.item = %(item)s"
        values["item"] = item

    rows = frappe.db.sql(f"""
        SELECT
            sle.item,
            i.item_name,
            sle.warehouse,
            SUM(sle.qty_change)  AS balance_qty,
            SUM(sle.stock_value) AS stock_value
        FROM `tabStock Ledger Entry` sle
        LEFT JOIN `tabItem` i ON i.name = sle.item
        WHERE
            sle.docstatus = 1
            AND DATE(sle.posting_datetime) <= %(to_date)s
            {warehouse_condition}
            {item_condition}
        GROUP BY
            sle.item, i.item_name, sle.warehouse
        HAVING
            balance_qty != 0
        ORDER BY
            sle.item, sle.warehouse
    """, values, as_dict=True)

    for row in rows:
        if row.balance_qty and row.balance_qty > 0:
            row.valuation_rate = row.stock_value / row.balance_qty
        else:
            row.valuation_rate = 0.0

    return rows


def get_leaf_warehouses(warehouse):
    """
    Return all non-group (leaf) warehouses under the given warehouse node.
    Uses Frappe nested sets (lft/rgt) — single SQL query, no recursion.
    """
    node = frappe.db.get_value(
        "Warehouse", warehouse,
        ["lft", "rgt", "is_group"],
        as_dict=True
    )
    if not node:
        return []

    rows = frappe.db.sql("""
        SELECT name
        FROM `tabWarehouse`
        WHERE
            lft  >= %(lft)s
            AND rgt  <= %(rgt)s
            AND is_group = 0
    """, {"lft": node.lft, "rgt": node.rgt}, as_dict=True)

    return [r.name for r in rows]
