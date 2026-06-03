# Copyright (c) 2026, Jyothi and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class GuestySettings(Document):


	@frappe.whitelist()
	def test_connection(self):
		"""Fetch a token and hit GET /v1/listings?limit=1 to prove the credentials work."""
		from property_management.property_management.guesty import client

		data = client.request("GET", "listings", params={"limit": 1})
		data = data or {}

		results = data.get("results") or []
		total = data.get("count")

		return {
			"ok": True,
			"count": total,
			"sample": (results[0].get("title") or results[0].get("nickname")) if results else None,
		}

	@frappe.whitelist()
	def sync_listings_now(self):
		"""Pull listings from Guesty into Property right now."""
		from property_management.property_management.guesty import sync_listings

		return sync_listings.run()

	@frappe.whitelist()
	def sync_reservations_now(self):
		"""Pull reservations from Guesty into Reservation right now."""
		from property_management.property_management.guesty import sync_reservations

		return sync_reservations.run()

	@frappe.whitelist()
	def generate_secret(self):
		"""Generate and store a random webhook secret."""
		import secrets

		self.webhook_secret = secrets.token_urlsafe(32)
		self.save(ignore_permissions=True)
		return {"ok": True}

	@frappe.whitelist()
	def register_webhook(self):
		"""Register this site's webhook endpoint with Guesty."""
		from property_management.property_management.guesty import webhook

		return webhook.register()

	@frappe.whitelist()
	def unregister_webhook(self):
		"""Delete the registered webhook from Guesty."""
		from property_management.property_management.guesty import webhook

		return webhook.unregister()
