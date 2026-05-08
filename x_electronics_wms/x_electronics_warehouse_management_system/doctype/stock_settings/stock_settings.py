# Copyright (c) 2026, Eammon Kiprotich and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class StockSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		default_valuation_method: DF.Literal["Moving Average", "FIFO", "LIFO"]
	# end: auto-generated types

	pass
