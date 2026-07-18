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
from frappe.utils import add_days, cint, cstr, flt, getdate, get_datetime, now_datetime

from property_management.property_management.guesty import client, sync_listings

PAGE_SIZE = 100

# Guesty's list endpoint returns a sparse default field set, so request what we map.
FIELDS = (
	"status checkIn checkOut checkInDateLocalized checkOutDateLocalized "
	"guestStay.status guestStay.updatedAt "
	"plannedArrival plannedDeparture nightsCount guestsCount numberOfGuests "
	"source integration confirmationCode listingId createdAt notes "
	"guestsDetails "
	"guest.firstName guest.lastName guest.fullName guest.email guest.phone "
	"money.currency money.fareAccommodation money.fareCleaning money.totalTaxes "
	"money.totalFees money.hostPayout money.balanceDue money.totalPaid "
	"money.ownerRevenue money.hostCommission money.netIncome "
	"money.securityDeposit money.securityDepositFee "
	"money.invoiceItems money.payments money.nightlyRates"
)

# Lifecycle status (Draft / Reserved / Awaiting Payment / Confirmed / Cancelled). Check-in/out are
# NOT lifecycle states — a checked-in guest is still a Confirmed booking; the stay
# status is carried separately in `guest_status` (see GUEST_STATUS_MAP).
STATUS_MAP = {
	"inquiry": "Draft",
	"reserved": "Reserved",
	"pending": "Reserved",
	"awaiting_payment": "Awaiting Payment",
	"confirmed": "Confirmed",
	"checkedin": "Confirmed",
	"checkedout": "Confirmed",
	"canceled": "Cancelled",
	"cancelled": "Cancelled",
	"declined": "Cancelled",
	"expired": "Cancelled",
}

# Guest *stay* status, separate from the lifecycle status above. Sourced from
# `guestStay.status` ("checked_in"), falling back to the lifecycle status for
# older payloads that folded the stay into it ("checkedin"). Keys are matched
# underscore-insensitively by _map_guest_status, so both spellings land here.
GUEST_STATUS_MAP = {
	"checkedin": "Checkin",
	"checkedout": "Checkout",
}

# Guesty's API returns SCREAMING_CASE payment enums (`SUCCEEDED`) while its UI
# shows friendly labels ("Approved"). Users reconcile against the Guesty UI, so
# store the label. Unmapped enums fall back to title-case via _map_payment_status.
PAYMENT_STATUS_MAP = {
	"succeeded": "Approved",
	"authorized": "Authorized",
	"pending": "Pending",
	"processing": "Processing",
	"failed": "Failed",
	"declined": "Declined",
	"canceled": "Canceled",
	"cancelled": "Canceled",
	"voided": "Voided",
	"refunded": "Refunded",
	"partially_refunded": "Partially Refunded",
	"chargeback": "Chargeback",
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

	details = reservation.get("guestsDetails") or {}
	fare = flt(money.get("fareAccommodation") or 0)
	fees = flt(money.get("totalFees") or 0)
	taxes = flt(money.get("totalTaxes") or 0)

	values = {
		"property_id": property_name,
		"reservation_type": "Booking",
		"reservation_status": _map_status(reservation.get("status")),
		"guest_status": _map_guest_status(reservation),
		"confirmation_code": cstr(reservation.get("confirmationCode") or ""),
		"reservation_check_in": getdate(check_in) if check_in else None,
		"reservation_check_out": getdate(check_out) if check_out else None,
		"no_of_nights": cint(reservation.get("nightsCount") or 0),
		"no_of_adults": cint(details.get("numberOfAdults") or reservation.get("guestsCount") or 0),
		"no_of_children": cint(details.get("numberOfChildren") or 0),
		"no_of_infants": cint(details.get("numberOfInfants") or 0),
		"first_name": guest.get("firstName") or guest.get("fullName") or "Guest",
		"last_name": cstr(guest.get("lastName") or ""),
		"email_id": cstr(guest.get("email") or ""),
		"phone_number": _clean_phone(guest.get("phone")),
		"source": _source(reservation),
		"check_in_time": cstr(reservation.get("plannedArrival") or ""),
		"check_out_time": cstr(reservation.get("plannedDeparture") or ""),
		"notes": _notes(reservation.get("notes")),
		# Money — Guesty is authoritative (these are NOT recomputed for synced docs).
		"reservation_item": fare,
		"reservation_management_fee": fees,
		"reservation_tax": taxes,
		"total_amount": _grand_total(money),
		"payment_status": _payment_status(money),
		"total_paid_amount": flt(money.get("totalPaid") or 0),
		"outstanding_amount": flt(money.get("balanceDue") or 0),
		"security_deposit": _security_deposit(money),
		"security_deposit_status": _security_deposit_status(money),
		"reservation_link": cstr(reservation.get("confirmationCode") or ""),
	}
	values.update(_folio_values(money))

	created_at = reservation.get("createdAt")
	if created_at:
		values["reservation_booking_date"] = getdate(created_at)

	line_items = _folio_line_items(money)
	accommodation_fare = _folio_accommodation_fare(money)
	folio_payments = _folio_payments(money)
	night_fare = _folio_night_fare(reservation, money)

	name = frappe.db.get_value("Reservation", {"guesty_id": guesty_id}, "name")

	if name:
		doc = frappe.get_doc("Reservation", name)
		# Never touch a reservation that was submitted/cancelled in ERPNext.
		if doc.docstatus != 0:
			return "skipped"
		doc.update(values)
		doc.set("reservation_line_items", line_items)
		doc.set("accommodation_fare", accommodation_fare)
		doc.set("folio_payments", folio_payments)
		doc.set("night_fare", night_fare)
		doc.flags.from_guesty = True
		doc.flags.ignore_mandatory = True  # guest phone may be absent
		doc.save(ignore_permissions=True)
		return "updated"

	doc = frappe.new_doc("Reservation")
	doc.update(values)
	doc.set("reservation_line_items", line_items)
	doc.set("accommodation_fare", accommodation_fare)
	doc.set("folio_payments", folio_payments)
	doc.set("night_fare", night_fare)
	doc.guesty_id = guesty_id
	doc.flags.from_guesty = True
	doc.flags.ignore_mandatory = True  # guest phone may be absent
	doc.insert(ignore_permissions=True)
	return "created"


def _folio_values(money):
	"""Top-level Guesty folio money breakdown → Reservation fields."""
	items = money.get("invoiceItems") or []
	total_price = sum(flt(i.get("amount") or 0) for i in items) if items else flt(money.get("hostPayout") or 0)
	payout = money.get("payout") or {}
	return {
		"folio_currency": _valid_currency(money.get("currency")),
		"folio_total_price": total_price,
		"folio_balance_due": flt(money.get("balanceDue") or 0),
		"folio_host_payout": flt(money.get("hostPayout") or 0),
		"folio_fare_cleaning": flt(money.get("fareCleaning") or 0),
		"folio_total_taxes": flt(money.get("totalTaxes") or 0),
		"folio_total_fees": flt(money.get("totalFees") or 0),
		# Payout breakdown (Guesty nests some of these under money.payout).
		"payout": flt(money.get("hostPayout") or payout.get("payout") or 0),
		"owners_revenue": flt(money.get("ownerRevenue") or payout.get("ownerRevenue") or 0),
		"your_commission": flt(money.get("hostCommission") or payout.get("hostCommission") or 0),
		"net_income": flt(money.get("netIncome") or payout.get("netIncome") or 0),
		"your_commission_inc_tax": flt(
			money.get("hostCommissionIncTax") or payout.get("hostCommissionIncTax") or 0
		),
		"channel_commission": flt(money.get("channelCommission") or payout.get("channelCommission") or 0),
		"channel_commission_tax": flt(
			money.get("channelCommissionTax") or payout.get("channelCommissionTax") or 0
		),
	}


def _folio_accommodation_fare(money):
	"""Guest Folio Breakdown *by item* → `accommodation_fare` child
	(Item / Amount / Tax / Total). Source: Guesty `money.invoiceItems`."""
	rows = []
	for item in (money.get("invoiceItems") or []):
		amount = flt(item.get("amount") or 0)
		tax = flt(item.get("tax") or item.get("amountTax") or 0)
		rows.append({
			"item": cstr(item.get("title") or item.get("normalType") or item.get("type") or ""),
			"amount": amount,
			"tax": tax,
			"total": amount + tax,
			"guesty_item_id": cstr(item.get("_id") or ""),
		})
	return rows


def _folio_line_items(money):
	"""Invoice line items / transactions → `reservation_line_items` child
	(Date / Transaction Type / Status / Payment Method / Amount).
	Source: Guesty `money.payments`."""
	rows = []
	for p in (money.get("payments") or []):
		paid = _parse_dt(p.get("paidAt") or p.get("createdAt"))
		rows.append({
			"title": getdate(paid) if paid else None,
			"transaction_type": cstr(p.get("type") or p.get("kind") or "Payment"),
			"status": _map_payment_status(p.get("status")),
			"payment_method": cstr(p.get("paymentMethod") or p.get("method") or ""),
			"amount": flt(p.get("amount") or 0),
			"guesty_item_id": cstr(p.get("_id") or ""),
		})
	return rows


def _folio_payments(money):
	rows = []
	currency = _valid_currency(money.get("currency"))
	for p in (money.get("payments") or []):
		rows.append({
			"payment_method": cstr(p.get("paymentMethod") or p.get("method") or ""),
			"amount": flt(p.get("amount") or 0),
			"currency": _valid_currency(p.get("currency")) or currency,
			"status": _map_payment_status(p.get("status")),
			"paid_at": _parse_dt(p.get("paidAt") or p.get("createdAt")),
			"note": cstr(p.get("note") or ""),
			"guesty_payment_id": cstr(p.get("_id") or ""),
		})
	return rows


def _folio_night_fare(reservation, money):
	"""Nightly breakdown → `night_fare` child (Date / Amount / Tax / Total).

	Prefers an explicit per-night array from Guesty (`money.nightlyRates` /
	`reservation.nightlyRates`); otherwise splits the accommodation fare evenly
	across the stay's nights.
	"""
	rows = []

	nightly = money.get("nightlyRates") or reservation.get("nightlyRates")
	if isinstance(nightly, list) and nightly:
		for n in nightly:
			if not isinstance(n, dict):
				continue
			amount = flt(n.get("rate") or n.get("price") or n.get("amount") or 0)
			tax = flt(n.get("tax") or 0)
			rows.append({
				"night_date": getdate(n.get("date")) if n.get("date") else None,
				"amount": amount,
				"tax": tax,
				"total": amount + tax,
			})
		return rows

	# Fallback: even split of the accommodation fare across the stay.
	check_in = reservation.get("checkInDateLocalized") or reservation.get("checkIn")
	check_out = reservation.get("checkOutDateLocalized") or reservation.get("checkOut")
	if not check_in or not check_out:
		return rows

	ci, co = getdate(check_in), getdate(check_out)
	nights = (co - ci).days
	if nights <= 0:
		return rows

	per_night = flt(money.get("fareAccommodation") or 0) / nights
	for i in range(nights):
		rows.append({
			"night_date": add_days(ci, i),
			"amount": per_night,
			"tax": 0,
			"total": per_night,
		})
	return rows


def _source(reservation):
	"""Booking source / channel (e.g. Airbnb, Booking.com, Direct)."""
	integration = reservation.get("integration") or {}
	return cstr(
		reservation.get("source")
		or integration.get("platform")
		or integration.get("integrationType")
		or reservation.get("channel")
		or ""
	)


def _notes(notes):
	"""Guesty notes may be a plain string or an object of note types → flatten."""
	if isinstance(notes, str):
		return notes
	if isinstance(notes, dict):
		parts = [f"{k}: {v}" for k, v in notes.items() if v]
		return "\n".join(parts)
	if isinstance(notes, list):
		return "\n".join(cstr(n) for n in notes if n)
	return ""


def _valid_currency(code):
	"""Return the currency code only if it exists as a Currency (avoids link errors)."""
	code = cstr(code).strip().upper()
	if code and frappe.db.exists("Currency", code):
		return code
	return None


def _parse_dt(value):
	"""Normalise a Guesty ISO-8601 timestamp (e.g. '2026-07-10T12:00:00Z') to a
	naive 'YYYY-MM-DD HH:MM:SS' that MySQL accepts."""
	value = cstr(value).strip()
	if not value:
		return None
	value = value.replace("T", " ")[:19]  # drop the 'Z' / timezone offset
	try:
		return get_datetime(value)
	except Exception:
		return None


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


def _grand_total(money):
	"""Guest total = sum of invoice items, **excluding the security deposit**
	(a hold, not a reservation charge). Falls back to fare + fees + taxes."""
	items = money.get("invoiceItems") or []
	if items:
		return sum(flt(i.get("amount") or 0) for i in items if not _is_deposit(i))
	return (
		flt(money.get("fareAccommodation") or 0)
		+ flt(money.get("totalFees") or 0)
		+ flt(money.get("totalTaxes") or 0)
	)


def _is_deposit(row):
	"""True when a Guesty invoice/payment line is the security deposit."""
	label = cstr(row.get("title") or row.get("type") or row.get("normalType")).lower()
	return "security" in label and "deposit" in label


def _security_deposit(money):
	"""Security deposit amount. Guesty delivers it as an **invoiceItem** (also
	tolerates a top-level money field / payment line)."""
	direct = flt(money.get("securityDeposit") or money.get("securityDepositFee") or 0)
	if direct:
		return direct
	for coll in (money.get("invoiceItems") or [], money.get("payments") or []):
		for row in coll:
			if _is_deposit(row):
				return flt(row.get("amount") or 0)
	return 0


def _security_deposit_status(money):
	"""Held / Charged / Released — from the security-deposit line's status, if any."""
	for coll in (money.get("payments") or [], money.get("invoiceItems") or []):
		for row in coll:
			if _is_deposit(row):
				return cstr(row.get("status") or "")
	return ""


def _payment_status(money):
	"""Derive the ERPNext payment status from Guesty money.

	Refund state is detected from a negative net or a refund line in payments;
	otherwise driven by isFullyPaid, falling back to balanceDue / totalPaid.
	"""
	paid = flt(money.get("totalPaid") or 0)
	balance = flt(money.get("balanceDue") or 0)
	refunded = sum(
		flt(p.get("amount") or 0)
		for p in (money.get("payments") or [])
		if flt(p.get("amount") or 0) < 0 or "refund" in cstr(p.get("status") or p.get("type")).lower()
	)
	if refunded < 0:
		# Some money was returned. Fully refunded when nothing net remains paid.
		return "Refunded" if paid <= 0 else "Partially Refunded"
	if paid <= 0:
		return "Not Paid"
	# Guesty states paid-in-full explicitly; balanceDue is the fallback for
	# older payloads that predate the flag.
	if money.get("isFullyPaid"):
		return "Fully Paid"
	if balance > 0:
		return "Partially Paid"
	return "Fully Paid"


def _map_status(status):
	return STATUS_MAP.get(cstr(status).strip().lower(), "Draft")


def _map_guest_status(reservation):
	"""Guest stay status from `guestStay.status`, else the lifecycle status."""
	stay = reservation.get("guestStay") or {}
	raw = cstr(stay.get("status") or reservation.get("status")).strip().lower()
	return GUEST_STATUS_MAP.get(raw.replace("_", ""), "Not Arrived")


def _map_payment_status(status):
	"""Guesty payment enum → the label Guesty's own UI shows (SUCCEEDED → Approved)."""
	raw = cstr(status).strip()
	if not raw:
		return ""
	return PAYMENT_STATUS_MAP.get(raw.lower(), raw.replace("_", " ").title())


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
