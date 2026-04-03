# Copyright (c) 2026, Eammon Kiprotich and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


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

	pass
