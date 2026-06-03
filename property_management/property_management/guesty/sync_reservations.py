# Copyright (c) 2026, Jyothi and contributors
# For license information, please see license.txt
"""Sync Guesty reservations → ERPNext Reservation.

Upserts reservations as **Draft** (record-only) keyed on the unique ``guesty_id``
field. We never submit them, so the Reservation controller's Sales Order / Invoice
side effects do not fire — Guesty stays authoritative for money. The
``from_guesty`` flag bypasses the future-date check and the base-amount recompute
in the controller. See GUESTY_INTEGRATION_PLAN.md §3.3, §4.4, §5.
"""

import re

import frappe
from frappe.utils import cint, cstr, flt, getdate, now_datetime

from property_management.property_management.guesty import client, sync_listings

PAGE_SIZE = 100

# Guesty's list endpoint returns a sparse default field set, so request what we map.
FIELDS = (
	"status checkIn checkOut checkInDateLocalized checkOutDateLocalized "
	"nightsCount guestsCount confirmationCode listingId createdAt "
	"guest.firstName guest.lastName guest.fullName guest.email guest.phone "
	"money.fareAccommodation money.hostPayout"
)

STATUS_MAP = {
	"inquiry": "Draft",
	"reserved": "Draft",
	"pending": "Draft",
	"awaiting_payment": "Draft",
	"confirmed": "Confirmed",
	"checkedin": "Checked In",
	"checkedout": "Checked Out",
	"canceled": "Cancelled",
	"cancelled": "Cancelled",
	"declined": "Cancelled",
	"expired": "Cancelled",
}



def run():
	"""Entry point for the scheduler and manual `bench execute`."""
	settings = frappe.get_single("Guesty Settings")
	if not settings.enabled or not settings.sync_reservations:
		return {"skipped": "disabled"}

	created = updated = skipped = failed = 0
	skip = 0

	while True:
		data = client.request(
			"GET", "reservations", params={"limit": PAGE_SIZE, "skip": skip, "fields": FIELDS}
		) or {}
		results = data.get("results") or []
		if not results:
			break

		for reservation in results:
			try:
				outcome = upsert_one(reservation)
				if outcome == "created":
					created += 1
				elif outcome == "updated":
					updated += 1
				else:
					skipped += 1
			except Exception:
				failed += 1
				frappe.log_error(
					frappe.get_traceback(),
					f"Guesty reservation sync failed: {reservation.get('_id')}",
				)

		skip += len(results)
		total = data.get("count")
		if total is not None and skip >= total:
			break

	settings.db_set("last_reservation_sync", now_datetime())
	frappe.db.commit()

	summary = {"created": created, "updated": updated, "skipped": skipped, "failed": failed}
	frappe.logger("guesty").info(f"Reservation sync done: {summary}")
	return summary


def upsert_one(reservation):
	"""Create or update a single Reservation (Draft) from a Guesty reservation dict."""
	guesty_id = reservation.get("_id")
	if not guesty_id:
		return None

	property_name = _get_property(reservation.get("listingId"))
	if not property_name:
		frappe.log_error(
			f"No Property for Guesty listing {reservation.get('listingId')}",
			f"Guesty reservation skipped: {guesty_id}",
		)
		return "skipped"

	guest = reservation.get("guest") or {}
	money = reservation.get("money") or {}

	check_in = reservation.get("checkInDateLocalized") or reservation.get("checkIn")
	check_out = reservation.get("checkOutDateLocalized") or reservation.get("checkOut")

	values = {
		"property_id": property_name,
		"reservation_type": "Booking",
		"reservation_status": _map_status(reservation.get("status")),
		"reservation_check_in": getdate(check_in) if check_in else None,
		"reservation_check_out": getdate(check_out) if check_out else None,
		"no_of_nights": cint(reservation.get("nightsCount") or 0),
		"no_of_adults": cint(reservation.get("guestsCount") or 0),
		"first_name": guest.get("firstName") or guest.get("fullName") or "Guest",
		"last_name": cstr(guest.get("lastName") or ""),
		"email_id": cstr(guest.get("email") or ""),
		"phone_number": _clean_phone(guest.get("phone")),
		"reservation_item": flt(money.get("fareAccommodation") or 0),
		"reservation_link": cstr(reservation.get("confirmationCode") or ""),
	}

	created_at = reservation.get("createdAt")
	if created_at:
		values["reservation_booking_date"] = getdate(created_at)

	name = frappe.db.get_value("Reservation", {"guesty_id": guesty_id}, "name")

	if name:
		doc = frappe.get_doc("Reservation", name)
		# Never touch a reservation that was submitted/cancelled in ERPNext.
		if doc.docstatus != 0:
			return "skipped"
		doc.update(values)
		doc.flags.from_guesty = True
		doc.flags.ignore_mandatory = True  # guest phone may be absent
		doc.save(ignore_permissions=True)
		return "updated"

	doc = frappe.new_doc("Reservation")
	doc.update(values)
	doc.guesty_id = guesty_id
	doc.flags.from_guesty = True
	doc.flags.ignore_mandatory = True  # guest phone may be absent
	doc.insert(ignore_permissions=True)
	return "created"


def _clean_phone(raw):
	"""Return an E.164 phone if valid, else "" (an empty Phone field passes validation).

	Keeping invalid/missing numbers empty — rather than a shared placeholder — avoids
	wrongly merging different phoneless guests into one Customer.
	"""
	raw = cstr(raw).strip()
	if not raw:
		return ""

	candidate = raw if raw.startswith("+") else "+" + re.sub(r"[^0-9]", "", raw)
	try:
		from phonenumbers import is_valid_number, parse

		if is_valid_number(parse(candidate)):
			return candidate
	except Exception:
		pass
	return ""


def _map_status(status):
	return STATUS_MAP.get(cstr(status).strip().lower(), "Draft")


def _get_property(listing_id):
	"""Resolve the Property for a listing, syncing the listing on demand if missing."""
	if not listing_id:
		return None

	name = frappe.db.get_value("Property", {"guesty_id": listing_id}, "name")
	if name:
		return name

	try:
		listing = client.request("GET", f"listings/{listing_id}")
		# /listings/{id} returns the object directly; tolerate a wrapped form too.
		if isinstance(listing, dict) and listing.get("results"):
			listing = listing["results"]
		if isinstance(listing, dict) and listing.get("_id"):
			sync_listings.upsert_one(listing)
			return frappe.db.get_value("Property", {"guesty_id": listing_id}, "name")
	except Exception:
		frappe.log_error(
			frappe.get_traceback(), f"Guesty on-demand listing fetch failed: {listing_id}"
		)

	return None
