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
            "label":     _("Date"),
            "fieldname": "posting_datetime",
            "fieldtype": "Datetime",
            "width":     160,
        },
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
            "width":     180,
        },
        {
            "label":     _("Warehouse"),
            "fieldname": "warehouse",
            "fieldtype": "Link",
            "options":   "Warehouse",
            "width":     160,
        },
        {
            "label":     _("Voucher Type"),
            "fieldname": "voucher_type",
            "fieldtype": "Data",
            "width":     120,
        },
        {
            "label":     _("Voucher No"),
            "fieldname": "voucher_no",
            "fieldtype": "Dynamic Link",
            "options":   "voucher_type",
            "width":     160,
        },
        {
            "label":     _("Qty Change"),
            "fieldname": "qty_change",
            "fieldtype": "Float",
            "width":     110,
        },
        {
            "label":     _("Balance Qty"),
            "fieldname": "balance_qty",
            "fieldtype": "Float",
            "width":     110,
        },
        {
            "label":     _("Valuation Rate"),
            "fieldname": "valuation_rate",
            "fieldtype": "Currency",
            "width":     130,
        },
        {
            "label":     _("Stock Value"),
            "fieldname": "stock_value",
            "fieldtype": "Currency",
            "width":     130,
        },
        {
            "label":     _("Balance Value"),
            "fieldname": "balance_value",
            "fieldtype": "Currency",
            "width":     130,
        },
    ]


def get_data(filters):
    conditions, values = build_conditions(filters)

    rows = frappe.db.sql(f"""
        SELECT
            sle.posting_datetime,
            sle.item,
            i.item_name,
            sle.warehouse,
            sle.voucher_type,
            sle.voucher_no,
            sle.qty_change,
            SUM(sle.qty_change)  OVER w AS balance_qty,
            sle.valuation_rate,
            sle.stock_value,
            SUM(sle.stock_value) OVER w AS balance_value
        FROM `tabStock Ledger Entry` sle
        LEFT JOIN `tabItem` i ON i.name = sle.item
        WHERE
            sle.docstatus = 1
            {conditions}
        WINDOW w AS (
            PARTITION BY sle.item, sle.warehouse
            ORDER BY sle.posting_datetime, sle.creation
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )
        ORDER BY sle.posting_datetime, sle.creation
    """, values, as_dict=True)

    return rows

def build_conditions(filters):
    conditions = ""
    values = {}

    if filters.get("item"):
        conditions += " AND sle.item = %(item)s"
        values["item"] = filters["item"]

    if filters.get("warehouse"):
        conditions += " AND sle.warehouse = %(warehouse)s"
        values["warehouse"] = filters["warehouse"]

    if filters.get("from_date"):
        conditions += " AND DATE(sle.posting_datetime) >= %(from_date)s"
        values["from_date"] = filters["from_date"]

    if filters.get("to_date"):
        conditions += " AND DATE(sle.posting_datetime) <= %(to_date)s"
        values["to_date"] = filters["to_date"]

    if filters.get("voucher_no"):
        conditions += " AND sle.voucher_no = %(voucher_no)s"
        values["voucher_no"] = filters["voucher_no"]

    return conditions, values
