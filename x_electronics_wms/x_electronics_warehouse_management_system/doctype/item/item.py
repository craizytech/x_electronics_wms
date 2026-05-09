# Copyright (c) 2026, Eammon Kiprotich and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class Item(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		is_stock_item: DF.Data | None
		item_code: DF.Data
		item_name: DF.Data
		stock_uom: DF.Data
		valuation_method: DF.Literal["Moving Average", "FIFO", "LIFO"]
	# end: auto-generated types

	def before_insert(self):
		"""
		Fetch Item valuation method from Stock Settings Doctype
		"""
		if not self.valuation_method:
			self.valuation_method = self._get_default_valuation_method()
	
	def validate(self):
		self.validate_valuation_method_not_changed()
	
	def _get_default_valuation_method(self):
		"""
		Fetch the default valuation otherwise set Moving Average to be the default valuation.
		"""
		try:
			default = frappe.db.get_single_value(
				"Stock Settings", "default_valuation_method"
			)
			return default or "Moving Average"
		except Exception:
			return "Moving Average"
		
	def validate_valuation_method_not_changed(self):
		"""
		This method ensures that once a stock ledger entry exists for an item
		the valuation method cannot be changed.
		"""
		if self.is_new():
			return
		
		old_method = frappe.db.get_value("Item", self.name, "valuation_method")

		if old_method == self.valuation_method:
			return
		
		sle_count = frappe.db.count(
			"Stock Ledger Entry",
			filters={"item": self.name, "docstatus": 1}
		)

		if sle_count > 0:
			frappe.throw(_(
				f"Cannot change valuation method for item '{self.name}'. "
				f"This item has {sle_count} existing stock ledger entries. "
				f"The valuation method can only be changed if no transactions exist. "
				f"To change it, cancel all stock entries for this item first."
			))
