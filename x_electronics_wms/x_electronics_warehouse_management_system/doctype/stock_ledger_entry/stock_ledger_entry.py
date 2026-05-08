# Copyright (c) 2026, Eammon Kiprotich and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class StockLedgerEntry(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		amended_from: DF.Link | None
		item: DF.Link
		posting_datetime: DF.Datetime
		qty_change: DF.Float
		stock_value: DF.Float
		valuation_rate: DF.Float
		voucher_no: DF.Data | None
		voucher_type: DF.Data | None
		warehouse: DF.Link
	# end: auto-generated types

	pass
