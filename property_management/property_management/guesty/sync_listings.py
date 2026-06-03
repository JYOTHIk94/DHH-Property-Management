# Copyright (c) 2026, Jyothi and contributors
# For license information, please see license.txt
"""Sync Guesty listings → ERPNext Property.

Pulls listings from the Guesty Open API and upserts them into Property, keyed on
the unique ``guesty_id`` field so re-runs never create duplicates. Read-only with
respect to Guesty (we only GET). See GUESTY_INTEGRATION_PLAN.md §3.1, §4.3.
"""

import frappe
from frappe.utils import cint, cstr, flt, getdate, now_datetime

from property_management.property_management.guesty import client

PAGE_SIZE = 100


def run():
	"""Entry point for the scheduler and manual `bench execute`."""
	settings = frappe.get_single("Guesty Settings")
	if not settings.enabled or not settings.sync_listings:
		return {"skipped": "disabled"}

	created = updated = failed = 0
	skip = 0

	while True:
		data = client.request("GET", "listings", params={"limit": PAGE_SIZE, "skip": skip}) or {}
		results = data.get("results") or []
		if not results:
			break

		for listing in results:
			try:
				outcome = upsert_one(listing)
				if outcome == "created":
					created += 1
				elif outcome == "updated":
					updated += 1
			except Exception:
				failed += 1
				frappe.log_error(
					frappe.get_traceback(),
					f"Guesty listing sync failed: {listing.get('_id')}",
				)

		skip += len(results)
		total = data.get("count")
		if total is not None and skip >= total:
			break

	settings.db_set("last_listing_sync", now_datetime())
	frappe.db.commit()

	summary = {"created": created, "updated": updated, "failed": failed}
	frappe.logger("guesty").info(f"Listing sync done: {summary}")
	return summary


def upsert_one(listing):
	"""Create or update a single Property from a Guesty listing dict."""
	guesty_id = listing.get("_id")
	if not guesty_id:
		return None

	prices = listing.get("prices") or {}

	values = {
		"property_type": _ensure_property_type(listing.get("propertyType")),
		"status": "Active" if listing.get("active") else "Inactive",
		"bedrooms": cstr(listing.get("bedrooms") or ""),
		"maximum_no_of_guests": cint(listing.get("accommodates") or 0),
		"base_price_per_night": flt(prices.get("basePrice") or 0),
		"area": flt(listing.get("areaSquareFeet") or 0),
	}

	created_at = listing.get("createdAt")
	if created_at:
		values["date"] = getdate(created_at)

	name = frappe.db.get_value("Property", {"guesty_id": guesty_id}, "name")

	if name:
		# Existing record: update fields but leave property_name (the record name) intact.
		doc = frappe.get_doc("Property", name)
		doc.update(values)
		doc.flags.from_guesty = True
		doc.save(ignore_permissions=True)
		return "updated"

	doc = frappe.new_doc("Property")
	doc.update(values)
	doc.guesty_id = guesty_id
	doc.property_name = _unique_property_name(listing, guesty_id)
	doc.flags.from_guesty = True
	doc.insert(ignore_permissions=True)
	return "created"


def _ensure_property_type(type_name):
	"""Return a valid Property Type, creating it on the fly, else the configured default."""
	type_name = (type_name or "").strip()
	if not type_name:
		return frappe.db.get_single_value("Guesty Settings", "default_property_type")

	if not frappe.db.exists("Property Type", type_name):
		frappe.get_doc({"doctype": "Property Type", "property_type": type_name}).insert(
			ignore_permissions=True
		)
	return type_name


def _unique_property_name(listing, guesty_id):
	"""Property.property_name is the unique record name; avoid collisions on insert."""
	base = cstr(listing.get("title") or listing.get("nickname") or guesty_id).strip()
	if not base:
		base = guesty_id
	if not frappe.db.exists("Property", base):
		return base
	return f"{base} ({guesty_id[-6:]})"
