# Copyright (c) 2026, Jyothi and contributors
# For license information, please see license.txt
"""Seed demo Guesty reservations to exercise the sync mapping on dhh.com.

Builds Guesty-shaped reservation payloads covering every money scenario and runs
them through the real ``sync_reservations.upsert_one`` path, so the seed exercises
the actual field mapping (payment_status, security deposit, the folio child
tables, the reservation/guest status split).

Run:    bench --site dhh.com execute property_management.property_management.guesty.seed_demo.run
Clean:  bench --site dhh.com execute property_management.property_management.guesty.seed_demo.cleanup

All records are tagged with a ``ZZSEED-`` guesty_id so cleanup is exact.
"""

import frappe

from property_management.property_management.guesty import sync_reservations

# Test_p2 — the one demo Property that carries a guesty_id (listing resolves offline).
LISTING = "6a27b3b56fe94c0019bf4c75"
PREFIX = "ZZSEED-"


def _guest(first):
	return {
		"firstName": first,
		"lastName": "Seed",
		"fullName": f"{first} Seed",
		"email": f"{first.lower()}@zzseed.local",
		"phone": "+971500000000",
	}


def _pay(amount, status, method="Card", at="2026-07-05T10:00:00Z", _id="p"):
	return {"_id": _id, "type": ("Refund" if amount < 0 else "Payment"),
	        "status": status, "paymentMethod": method, "amount": amount, "paidAt": at}


def _items(fare, cleaning, tax=0.0, deposit=0.0):
	rows = [
		{"_id": "acc", "title": "Accommodation fare", "amount": fare, "tax": tax},
		{"_id": "cln", "title": "Cleaning fee", "amount": cleaning},
	]
	if deposit:
		rows.append({"_id": "dep", "title": "Security Deposit", "amount": deposit, "status": "HELD"})
	return rows


def _nights(start_day, n, rate=1000.0, tax=50.0):
	return [{"date": f"2026-10-{start_day + i:02d}", "rate": rate, "tax": tax} for i in range(n)]


def _money(fare, cleaning, paid, tax=0.0, deposit=0.0, payments=None, nightly=None):
	total = fare + cleaning  # deposit excluded from the reservation total
	return {
		"currency": "AED",
		"fareAccommodation": fare,
		"fareCleaning": cleaning,
		"totalTaxes": tax,
		"totalFees": cleaning,
		"hostPayout": total,
		"totalPaid": paid,
		"balanceDue": total - paid,
		"invoiceItems": _items(fare, cleaning, tax, deposit),
		"payments": payments or [],
		"nightlyRates": nightly or [],
	}


def _scenarios():
	"""(idx, label, guesty status, check_in, check_out, nights, money)."""
	return [
		("01", "Confirmed — Unpaid", "confirmed", "2026-08-03", "2026-08-06", 3,
		 _money(3000, 300, 0)),
		("02", "Confirmed — Partly Paid", "confirmed", "2026-08-10", "2026-08-13", 3,
		 _money(3000, 300, 1500, payments=[_pay(1500, "SUCCEEDED", _id="p1")])),
		("03", "Confirmed — Fully Paid", "confirmed", "2026-08-17", "2026-08-20", 3,
		 _money(3000, 300, 3300, payments=[_pay(3300, "SUCCEEDED", _id="p1")])),
		("04", "Confirmed — Partial Refund", "confirmed", "2026-08-24", "2026-08-27", 3,
		 _money(3000, 300, 2500, payments=[_pay(3300, "SUCCEEDED", _id="p1"),
		                                   _pay(-800, "REFUNDED", at="2026-07-08T09:00:00Z", _id="p2")])),
		("05", "Confirmed — Full Refund", "confirmed", "2026-09-01", "2026-09-04", 3,
		 _money(3000, 300, 0, payments=[_pay(3300, "SUCCEEDED", _id="p1"),
		                                _pay(-3300, "REFUNDED", at="2026-07-08T09:00:00Z", _id="p2")])),
		("06", "Cancelled", "canceled", "2026-09-08", "2026-09-11", 3,
		 _money(3000, 300, 0)),
		("07", "Fully Paid + Security Deposit", "confirmed", "2026-09-15", "2026-09-18", 3,
		 _money(4000, 400, 4400, tax=200, deposit=2000, payments=[_pay(4400, "SUCCEEDED", _id="p1")])),
		("08", "Checked-in (guest_status)", "checkedin", "2026-09-22", "2026-09-25", 3,
		 _money(3000, 300, 3300, payments=[_pay(3300, "SUCCEEDED", _id="p1")])),
		("09", "Multi-night (nightly breakdown)", "confirmed", "2026-10-01", "2026-10-06", 5,
		 _money(5000, 500, 5500, tax=250, payments=[_pay(5500, "SUCCEEDED", _id="p1")],
		        nightly=_nights(1, 5))),
	]


def run():
	names = ["Alice", "Bob", "Carol", "Dave", "Eve", "Frank", "Grace", "Heidi", "Ivan"]
	created = []
	for (idx, label, status, ci, co, nights, money), guest_name in zip(_scenarios(), names):
		payload = {
			"_id": f"{PREFIX}RES-{idx}",
			"status": status,
			"confirmationCode": f"ZZSEED{idx}",
			"listingId": LISTING,
			"checkInDateLocalized": ci,
			"checkOutDateLocalized": co,
			"nightsCount": nights,
			"guestsCount": 2,
			"guestsDetails": {"numberOfAdults": 2, "numberOfChildren": 1, "numberOfInfants": 0},
			"guest": _guest(guest_name),
			"source": "Direct",
			"createdAt": "2026-07-01T10:00:00Z",
			"notes": label,
			"money": money,
		}
		outcome = sync_reservations.upsert_one(payload)
		name = frappe.db.get_value("Reservation", {"guesty_id": payload["_id"]}, "name")
		created.append((idx, label, outcome, name))

	frappe.db.commit()
	print(f"Seeded {len(created)} reservations:")
	for idx, label, outcome, name in created:
		r = frappe.get_doc("Reservation", name)
		print(f"  {idx} {label:34} -> {name} | {r.reservation_status}/{r.guest_status} | "
		      f"pay={r.payment_status} paid={r.total_paid_amount:.0f} due={r.outstanding_amount:.0f} "
		      f"total={r.total_amount:.0f} deposit={r.security_deposit:.0f} | "
		      f"acc={len(r.accommodation_fare)} night={len(r.night_fare)} "
		      f"lines={len(r.reservation_line_items)} pmts={len(r.folio_payments)}")
	return {"seeded": len(created)}


def cleanup():
	names = frappe.get_all("Reservation", filters={"guesty_id": ["like", f"{PREFIX}%"]}, pluck="name")
	for name in names:
		frappe.delete_doc("Reservation", name, force=True, ignore_permissions=True)
	frappe.db.commit()
	print(f"Deleted {len(names)} seed reservations.")
	return {"deleted": len(names)}
