# Copyright (c) 2026, Jyothi and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class PropertyVacancyLog(Document):
	def validate(self):

		if self.occupied_guests > self.maximum_no_of_guests:
			frappe.throw("Occupied guests cannot exceed maximum number of guests.")
		self.calculate_vacancy_count()
		if (self.available or 0) <= 0:
			frappe.throw("No availability for this property")




	def calculate_vacancy_count(self):
		if self.maximum_no_of_guests and self.occupied_guests is not None:
			self.available = self.maximum_no_of_guests - self.occupied_guests