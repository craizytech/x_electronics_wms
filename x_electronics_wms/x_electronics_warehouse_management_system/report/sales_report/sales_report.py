"""
Sales Report
------------
Shows revenue, COGS, and gross profit per Sales Invoice line.
Supports filtering by date range, item, and customer.
Includes summary totals at the bottom.
"""

import frappe
from frappe import _


def execute(filters=None):
    filters = filters or {}
    columns = get_columns()
    data    = get_data(filters)
    return columns, data


def get_columns():
    return [
        {
            "label":     _("Date"),
            "fieldname": "posting_date",
            "fieldtype": "Date",
            "width":     110,
        },
        {
            "label":     _("Invoice"),
            "fieldname": "invoice",
            "fieldtype": "Link",
            "options":   "Sales Invoice",
            "width":     160,
        },
        {
            "label":     _("Customer"),
            "fieldname": "customer",
            "fieldtype": "Data",
            "width":     150,
        },
        {
            "label":     _("Item"),
            "fieldname": "item",
            "fieldtype": "Link",
            "options":   "Item",
            "width":     130,
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
            "width":     150,
        },
        {
            "label":     _("Qty"),
            "fieldname": "qty",
            "fieldtype": "Float",
            "width":     80,
        },
        {
            "label":     _("Selling Rate"),
            "fieldname": "rate",
            "fieldtype": "Currency",
            "width":     120,
        },
        {
            "label":     _("Revenue"),
            "fieldname": "amount",
            "fieldtype": "Currency",
            "width":     120,
        },
        {
            "label":     _("Valuation Rate"),
            "fieldname": "valuation_rate",
            "fieldtype": "Currency",
            "width":     130,
        },
        {
            "label":     _("COGS"),
            "fieldname": "cogs",
            "fieldtype": "Currency",
            "width":     120,
        },
        {
            "label":     _("Gross Profit"),
            "fieldname": "gross_profit",
            "fieldtype": "Currency",
            "width":     130,
        },
        {
            "label":     _("Margin %"),
            "fieldname": "margin_pct",
            "fieldtype": "Percent",
            "width":     100,
        },
    ]


def get_data(filters):
    conditions, values = build_conditions(filters)

    rows = frappe.db.sql(f"""
        SELECT
            si.posting_date,
            si.name                 AS invoice,
            si.customer,
            sid.item,
            sid.item_name,
            sid.warehouse,
            sid.qty,
            sid.rate,
            sid.amount,
            sid.valuation_rate,
            sid.cogs,
            (sid.amount - sid.cogs) AS gross_profit
        FROM `tabSales Invoice` si
        INNER JOIN `tabSales Invoice Detail` sid ON sid.parent = si.name
        WHERE
            si.docstatus = 1
            {conditions}
        ORDER BY si.posting_date DESC, si.name DESC
    """, values, as_dict=True)

    # Compute margin % and add summary row
    total_revenue = 0.0
    total_cogs    = 0.0
    total_profit  = 0.0

    for row in rows:
        row.gross_profit = (row.amount or 0) - (row.cogs or 0)
        if row.amount and row.amount > 0:
            row.margin_pct = (row.gross_profit / row.amount) * 100
        else:
            row.margin_pct = 0.0
        total_revenue += row.amount or 0
        total_cogs    += row.cogs    or 0
        total_profit  += row.gross_profit

    # Add a blank separator then totals row
    if rows:
        rows.append({})  # blank row
        rows.append({
            "posting_date":  None,
            "invoice":       None,
            "customer":      "TOTAL",
            "item":          None,
            "item_name":     None,
            "warehouse":     None,
            "qty":           None,
            "rate":          None,
            "amount":        total_revenue,
            "valuation_rate":None,
            "cogs":          total_cogs,
            "gross_profit":  total_profit,
            "margin_pct":    (total_profit / total_revenue * 100) if total_revenue else 0,
        })

    return rows


def build_conditions(filters):
    conditions = ""
    values     = {}

    if filters.get("from_date"):
        conditions += " AND si.posting_date >= %(from_date)s"
        values["from_date"] = filters["from_date"]

    if filters.get("to_date"):
        conditions += " AND si.posting_date <= %(to_date)s"
        values["to_date"] = filters["to_date"]

    if filters.get("customer"):
        conditions += " AND si.customer = %(customer)s"
        values["customer"] = filters["customer"]

    if filters.get("item"):
        conditions += " AND sid.item = %(item)s"
        values["item"] = filters["item"]

    if filters.get("warehouse"):
        conditions += " AND sid.warehouse = %(warehouse)s"
        values["warehouse"] = filters["warehouse"]

    return conditions, values
