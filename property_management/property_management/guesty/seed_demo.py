# Copyright (c) 2026, Jyothi and contributors
# For license information, please see license.txt
"""Seed demo Guesty reservations to exercise the sync mapping on dhh.com.

Builds Guesty-shaped reservation payloads covering every money scenario and runs
them through the real ``sync_reservations.upsert_one`` path, so the seed exercises
the actual field mapping (payment_status, security deposit, the folio child
tables, the reservation/guest status split).

Two sets are seeded:

* **Mapping** (01-09) — one payload each, to check how a folio lands on the
  Reservation.
* **Billing** (10-19) — *staged* payloads that replay the webhook sequence of a
  real stay (confirmed → checked in → checked out → refunded / cancelled), so the
  accounting documents are raised by the real ``on_update`` hooks: a **draft
  Sales Invoice** at check-in, **submit + Payment Entry** at checkout, and a
  **Credit Note + refund Payment Entry** on a refund or a paid cancellation.

* **Manual** (21) — a reservation keyed in by hand: no folio, so it is billed
  from its own amounts and settled in full at departure.
* **Item routing** (22-26) — how a Guesty charge resolves to an ERPNext Item:
  reuse by code, reuse by name, mint under Services, no duplicates.

Run:      bench --site dhh.com execute property_management.property_management.guesty.seed_demo.run
Billing:  bench --site dhh.com execute property_management.property_management.guesty.seed_demo.run_billing
Items:    bench --site dhh.com execute property_management.property_management.guesty.seed_demo.run_items
Clean:    bench --site dhh.com execute property_management.property_management.guesty.seed_demo.cleanup

All records are tagged with a ``ZZSEED-`` guesty_id so cleanup is exact; cleanup
also cancels and deletes the invoices, payments and credit notes they raised.
"""

import frappe

from property_management.property_management.guesty import sync_reservations

# Test_p2 — the one demo Property that carries a guesty_id (listing resolves offline).
LISTING = "6a27b3b56fe94c0019bf4c75"
PREFIX = "ZZSEED-"

# Mode of Payment the billing hooks fall back to; its company account must exist
# or the Payment Entry cannot be raised (see _preflight).
MODE_OF_PAYMENT = "Cash"


def _guest(first, idx):
	# Unique phone per guest: get_or_create_customer matches on email *or* phone,
	# so a shared number would collapse every seed booking onto one Customer.
	return {
		"firstName": first,
		"lastName": "Seed",
		"fullName": f"{first} Seed",
		"email": f"{first.lower()}@zzseed.local",
		"phone": f"+971500000{idx}",
	}


def _pay(amount, status, method="Card", at="2026-07-05T10:00:00Z", _id="p", desc=None):
	return {"_id": _id, "type": ("Refund" if amount < 0 else "Payment"),
	        "status": status, "paymentMethod": method, "amount": amount, "paidAt": at,
	        "description": desc or ("Refund to guest" if amount < 0 else "Reservation payment")}


def _extra(title, amount, normal_type, second_identifier, description=None, _id=None):
	"""An additional folio charge (late checkout, extra guest, pet fee ...)."""
	return {"_id": _id or second_identifier.lower(), "title": title, "amount": amount,
	        "description": description or title,
	        "normalType": normal_type, "secondIdentifier": second_identifier}


def _items(fare, cleaning, tax=0.0, deposit=0.0, extras=None):
	"""Guesty-shaped money.invoiceItems.

	Carries the five fields Guesty requires on an invoice item — title,
	description, amount, normalType, secondIdentifier — so the seed exercises the
	real `_folio_invoice_items` mapping rather than a reduced form of it.
	"""
	rows = [
		{"_id": "acc", "title": "Accommodation fare", "amount": fare, "tax": tax,
		 "description": "Accommodation fare for the stay",
		 "normalType": "AF", "secondIdentifier": "ACCOMMODATION"},
		{"_id": "cln", "title": "Cleaning fee", "amount": cleaning,
		 "description": "One-off cleaning charge",
		 "normalType": "CF", "secondIdentifier": "CLEANING"},
	]
	rows.extend(extras or [])
	if deposit:
		rows.append({"_id": "dep", "title": "Security Deposit", "amount": deposit, "status": "HELD",
		             "description": "Refundable security deposit hold",
		             "normalType": "SD", "secondIdentifier": "SECURITY_DEPOSIT"})
	return rows


def _nights(start_day, n, rate=1000.0, tax=50.0):
	return [{"date": f"2026-10-{start_day + i:02d}", "rate": rate, "tax": tax} for i in range(n)]


def _money(fare, cleaning, paid, tax=0.0, deposit=0.0, payments=None, nightly=None, extras=None):
	extras = extras or []
	# deposit excluded from the reservation total — it is a hold, not a charge.
	total = fare + cleaning + sum(e["amount"] for e in extras)

	# Payout economics — how the money divides AFTER the guest pays. These are
	# host-side, never guest charges. Modelled here so the seed exercises the
	# payout tab: 15% host commission, 5% channel commission, 5% tax on each.
	host_commission = round(total * 0.15, 2)
	channel_commission = round(total * 0.05, 2)
	channel_commission_tax = round(channel_commission * 0.05, 2)
	host_commission_inc_tax = round(host_commission * 1.05, 2)
	owner_revenue = round(total - host_commission - channel_commission, 2)

	return {
		"currency": "AED",
		"fareAccommodation": fare,
		"fareCleaning": cleaning,
		"totalTaxes": tax,
		"totalFees": cleaning,
		"hostPayout": total,
		"totalPaid": paid,
		"balanceDue": total - paid,
		"isFullyPaid": paid >= total,
		"ownerRevenue": owner_revenue,
		"hostCommission": host_commission,
		"hostCommissionIncTax": host_commission_inc_tax,
		"channelCommission": channel_commission,
		"channelCommissionTax": channel_commission_tax,
		"netIncome": round(host_commission - channel_commission_tax, 2),
		"invoiceItems": _items(fare, cleaning, tax, deposit, extras),
		"payments": payments or [],
		"nightlyRates": nightly or [],
	}


# ----------------------------------------------------------------------
# Folio shorthands for the billing scenarios
# ----------------------------------------------------------------------

def _unpaid(fare=3000, cleaning=300, **kw):
	return _money(fare, cleaning, 0, **kw)


def _part_paid(paid, fare=3000, cleaning=300, **kw):
	return _money(fare, cleaning, paid, payments=[_pay(paid, "SUCCEEDED", _id="p1")], **kw)


def _paid(fare=3000, cleaning=300, extras=None, **kw):
	total = fare + cleaning + sum(e["amount"] for e in (extras or []))
	return _money(fare, cleaning, total, extras=extras,
	              payments=[_pay(total, "SUCCEEDED", _id="p1")], **kw)


def _refunded(refund, fare=3000, cleaning=300, **kw):
	"""Fully paid, then `refund` given back — a negative line in money.payments,
	which is the only way Guesty reports a refund."""
	total = fare + cleaning
	return _money(fare, cleaning, total - refund,
	              payments=[_pay(total, "SUCCEEDED", _id="p1"),
	                        _pay(-refund, "REFUNDED", at="2026-07-08T09:00:00Z", _id="p2")], **kw)


LATE_FEE = _extra("Late checkout fee", 250, "AFE", "LATE_CHECKOUT",
                  "Departure after the standard checkout time")


def _scenarios():
	"""Mapping set — (idx, label, guesty status, check_in, check_out, nights, money)."""
	return [
		("01", "Confirmed — Not Paid", "confirmed", "2026-08-03", "2026-08-06", 3,
		 _money(3000, 300, 0)),
		("02", "Confirmed — Partially Paid", "confirmed", "2026-08-10", "2026-08-13", 3,
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


def _billing_scenarios():
	"""Billing set — (idx, label, channel, guest, check_in, check_out, nights, stages).

	`stages` replays the webhook sequence Guesty would send for that booking; each
	stage is a (guesty status, money) pair pushed through `upsert_one`, so the
	documents are raised by the real `Reservation.on_update` hooks rather than
	built here. What each stage produces:

	    confirmed  → folio mapped, no accounting document
	    checkedin  → DRAFT Sales Invoice, but only once money has been paid
	    checkedout → invoice submitted + Payment Entry for what Guesty collected
	    refund     → Credit Note + refund Payment Entry
	    canceled   → draft invoice deleted, or submitted invoice reversed by a CN

	Expected outcome per scenario is in the label, so a run can be read as a
	pass/fail sheet against the printed document columns.
	"""
	return [
		("10", "Checkin, Fully Paid -> draft SI", "airbnb2", "Judy",
		 "2026-11-02", "2026-11-05", 3,
		 [("confirmed", _paid()), ("checkedin", _paid())]),

		("11", "Checkin, Partially Paid -> draft SI", "bookingCom", "Karl",
		 "2026-11-06", "2026-11-09", 3,
		 [("confirmed", _part_paid(1500)), ("checkedin", _part_paid(1500))]),

		# The billing gate: in residence but nothing collected, so nothing is billed.
		("12", "Checkin, Not Paid -> NO SI", "manual", "Leo",
		 "2026-11-10", "2026-11-13", 3,
		 [("confirmed", _unpaid()), ("checkedin", _unpaid())]),

		("13", "Checkout, Fully Paid -> SI + PE", "expedia", "Mallory",
		 "2026-11-14", "2026-11-17", 3,
		 [("confirmed", _paid()), ("checkedin", _paid()), ("checkedout", _paid())]),

		# totalPaid (6400) includes the 2000 deposit hold, which is NOT invoiced —
		# the Payment Entry must be capped at the 4400 invoice outstanding.
		("14", "Checkout + deposit -> PE capped at invoice", "homeaway2", "Niaj",
		 "2026-11-18", "2026-11-21", 3,
		 [("confirmed", _money(4000, 400, 6400, tax=200, deposit=2000,
		                       payments=[_pay(6400, "SUCCEEDED", _id="p1")])),
		  ("checkedin", _money(4000, 400, 6400, tax=200, deposit=2000,
		                       payments=[_pay(6400, "SUCCEEDED", _id="p1")])),
		  ("checkedout", _money(4000, 400, 6400, tax=200, deposit=2000,
		                        payments=[_pay(6400, "SUCCEEDED", _id="p1")]))]),

		# A fee added mid-stay must flow into the still-draft invoice (3300 -> 3550).
		("15", "Mid-stay fee -> draft SI rebuilt", "agoda", "Olivia",
		 "2026-11-22", "2026-11-25", 3,
		 [("confirmed", _paid()), ("checkedin", _paid()),
		  ("checkedin", _part_paid(3300, extras=[LATE_FEE])),
		  ("checkedout", _part_paid(3300, extras=[LATE_FEE]))]),

		("16", "Checkout then partial refund -> CN + refund PE", "airbnb2", "Peggy",
		 "2026-11-26", "2026-11-29", 3,
		 [("confirmed", _paid()), ("checkedin", _paid()), ("checkedout", _paid()),
		  ("checkedout", _refunded(800))]),

		("17", "Checkout then full refund -> CN + refund PE", "", "Quentin",
		 "2026-12-01", "2026-12-04", 3,
		 [("confirmed", _paid()), ("checkedin", _paid()), ("checkedout", _paid()),
		  ("checkedout", _refunded(3300))]),

		# Cancelled while the invoice is still draft — it never posted, so it is
		# deleted outright rather than reversed.
		("18", "Checkin then cancelled -> draft SI deleted", "tripAdvisor", "Rupert",
		 "2026-12-05", "2026-12-08", 3,
		 [("confirmed", _paid()), ("checkedin", _paid()), ("canceled", _paid())]),

		# Cancelled after the invoice posted — reversed with a Credit Note. No cash
		# went back (no negative payment line), so there is no refund Payment Entry.
		("19", "Checkout then cancelled -> CN reversal", "bookingCom", "Sybil",
		 "2026-12-09", "2026-12-12", 3,
		 [("confirmed", _paid()), ("checkedin", _paid()), ("checkedout", _paid()),
		  ("canceled", _paid())]),

		# Never paid, but the stay happened: the payment gate holds during the stay
		# and lifts at checkout, so the invoice posts and stands outstanding.
		("20", "Checkout, Not Paid -> SI outstanding, no PE", "manual", "Trent",
		 "2026-12-13", "2026-12-16", 3,
		 [("confirmed", _unpaid()), ("checkedin", _unpaid()), ("checkedout", _unpaid())]),
	]


# A reservation keyed in by hand: no guesty_id, no folio, so it is billed from
# its own amounts (rental + management fee) and settled in full at departure.
MANUAL_SEED = {
	"idx": "21",
	"label": "Manual (no Guesty folio) -> SI + PE at checkout",
	"first_name": "Manual",
	"last_name": "Seed",
	"email": "manual@zzseed.local",
	"phone": "+971500000021",
	"check_in": "2026-12-17",
	"check_out": "2026-12-20",
	"nights": 3,
	# The rental line is derived from the Property's nightly rate on validate;
	# only the management fee is taken from here.
	"fee": 300,
}


# ----------------------------------------------------------------------
# Item-routing scenarios (22-26)
# ----------------------------------------------------------------------
#
# A Guesty charge is billed against an Item whose code AND name are the charge
# title. An Item that already carries that code — or that name under a different
# code — is reused; only a genuinely new charge mints an Item, in Services.
#
# Every seed-only charge title starts with ZZSEED so cleanup can find the Items
# it minted without touching the Item Master.

# Pre-created by the seed so a charge can be matched to an Item whose *name*
# matches while its code does not.
ITEM_FIXTURE = {"item_code": "ZZSEED-XBED-001", "item_name": "ZZSEED Extra bed"}

# 140 is the Frappe limit; this title is longer, so the code must be truncated
# to exactly 140 characters and still resolve to a single Item.
LONG_TITLE = "ZZSEED Very long charge title " + ("that keeps going and going " * 6)


def _item_scenarios():
	"""(idx, label, guest, check_in, check_out, charges, expected)

	`charges` are extra folio lines on top of the standard accommodation +
	cleaning pair; `expected` is the Item each extra charge must resolve to.
	Each is synced confirmed → checkedin, which is enough to raise the draft
	invoice whose lines carry the routing decision.
	"""
	return [
		("22", "Charge titled like an existing Item -> reuse", "Ursula",
		 "2026-12-21", "2026-12-24",
		 [_extra("Service item", 150, "AFE", "MANAGEMENT", "Charge already in the Item Master")],
		 ["Service item"]),

		("23", "New charge title -> new Item in Services", "Victor",
		 "2026-12-25", "2026-12-28",
		 [_extra("ZZSEED Pet fee", 200, "AFE", "PET", "Pet staying with the guest")],
		 ["ZZSEED Pet fee"]),

		# Same charge as 23 on a different booking: the Item is reused, never
		# duplicated — this is what keeps revenue reporting on one line.
		("24", "Same charge again -> reuses Item, no duplicate", "Walter",
		 "2026-12-29", "2027-01-01",
		 [_extra("ZZSEED Pet fee", 200, "AFE", "PET", "Pet staying with the guest")],
		 ["ZZSEED Pet fee"]),

		# The Item exists under a different code, matched on item_name.
		("25", "Title matches an Item name, not its code -> reuse", "Xena",
		 "2027-01-02", "2027-01-05",
		 [_extra(ITEM_FIXTURE["item_name"], 120, "AFE", "EXTRA_BED", "Extra bed for the stay")],
		 [ITEM_FIXTURE["item_code"]]),

		("26", "Over-long title -> truncated to 140 chars", "Yuri",
		 "2027-01-06", "2027-01-09",
		 [_extra(LONG_TITLE, 90, "AFE", "MISC", "Charge with an unusually long title")],
		 [LONG_TITLE[:140]]),
	]


# Guesty's raw channel identifiers, spread across the mapping scenarios so the
# demo data exercises the Channel / Channel Type mapping instead of being
# uniformly Direct.
CHANNELS = ["airbnb2", "bookingCom", "manual", "expedia", "homeaway2",
            "agoda", "", "airbnb2", "tripAdvisor"]

MAPPING_NAMES = ["Alice", "Bob", "Carol", "Dave", "Eve", "Frank", "Grace", "Heidi", "Ivan"]


def _payload(idx, status, ci, co, nights, money, guest_name, channel, label):
	return {
		"_id": f"{PREFIX}RES-{idx}",
		"status": status,
		"confirmationCode": f"ZZSEED{idx}",
		"listingId": LISTING,
		"checkInDateLocalized": ci,
		"checkOutDateLocalized": co,
		"nightsCount": nights,
		"guestsCount": 2,
		"guestsDetails": {"numberOfAdults": 2, "numberOfChildren": 1, "numberOfInfants": 0},
		"guest": _guest(guest_name, idx),
		"source": channel,
		"createdAt": "2026-07-01T10:00:00Z",
		"notes": label,
		"money": money,
	}


def _preflight():
	"""Fail early and clearly when the site cannot raise the accounting documents."""
	company = frappe.db.get_single_value("Property Settings", "default_company")
	if not company:
		frappe.throw("Set a Default Company in Property Settings before seeding.")

	account = frappe.db.get_value(
		"Mode of Payment Account",
		{"parent": MODE_OF_PAYMENT, "company": company},
		"default_account",
	)
	if not account:
		frappe.throw(
			f"Mode of Payment '{MODE_OF_PAYMENT}' has no default account for company "
			f"'{company}' — the checkout Payment Entry cannot be raised."
		)
	return company


def run():
	"""Seed every set: mapping (01-09), billing (10-20), manual (21), items (22-26)."""
	mapping = run_mapping(commit=False)
	billing = run_billing(commit=False)
	manual = run_manual(commit=False)
	items = run_items(commit=False)
	frappe.db.commit()
	return {"seeded": mapping["seeded"] + billing["seeded"] + manual["seeded"] + items["seeded"],
	        "item_scenarios_passed": items["passed"]}


def run_mapping(commit=True):
	"""Seed the folio-mapping scenarios — one payload each, no accounting documents."""
	created = []
	for (idx, label, status, ci, co, nights, money), guest_name, channel in zip(
		_scenarios(), MAPPING_NAMES, CHANNELS
	):
		payload = _payload(idx, status, ci, co, nights, money, guest_name, channel, label)
		sync_reservations.upsert_one(payload)
		created.append((idx, label, frappe.db.get_value(
			"Reservation", {"guesty_id": payload["_id"]}, "name")))

	if commit:
		frappe.db.commit()

	print(f"\nSeeded {len(created)} mapping reservations:")
	for idx, label, name in created:
		r = frappe.get_doc("Reservation", name)
		print(f"  {idx} {label:34} -> {name} | {r.source}/{r.channel_type} | "
		      f"{r.reservation_status}/{r.guest_status} | "
		      f"pay={r.payment_status} paid={r.total_paid_amount:.0f} due={r.outstanding_amount:.0f} "
		      f"total={r.total_amount:.0f} deposit={r.security_deposit:.0f} | "
		      f"acc={len(r.accommodation_fare)} night={len(r.night_fare)} "
		      f"lines={len(r.reservation_line_items)} inv={len(r.invoice_items)}")
	return {"seeded": len(created)}


def run_billing(commit=True):
	"""Seed the staged billing scenarios — Sales Invoices, Payment Entries, Credit Notes.

	Each stage is a separate `upsert_one`, exactly as Guesty's webhooks arrive, so
	the documents come out of the Reservation's own hooks. A stage that throws is
	reported against its scenario and the rest continue — one broken scenario must
	not take the whole seed down.
	"""
	company = _preflight()
	print(f"\nSeeding billing scenarios for company '{company}' ...")

	created = []
	for idx, label, channel, guest_name, ci, co, nights, stages in _billing_scenarios():
		error = None
		for status, money in stages:
			payload = _payload(idx, status, ci, co, nights, money, guest_name, channel, label)
			try:
				sync_reservations.upsert_one(payload)
			except Exception as e:
				# Roll back only the failed stage; earlier stages are already committed.
				frappe.db.rollback()
				error = f"{type(e).__name__}: {e}"
				break
			frappe.db.commit()

		created.append((idx, label, frappe.db.get_value(
			"Reservation", {"guesty_id": f"{PREFIX}RES-{idx}"}, "name"), error))

	if commit:
		frappe.db.commit()

	print(f"\nSeeded {len(created)} billing reservations:")
	for idx, label, name, error in created:
		if error:
			print(f"  {idx} {label:44} -> FAILED  {error}")
			continue
		r = frappe.get_doc("Reservation", name)
		print(f"  {idx} {label:44} -> {name} | {r.reservation_status}/{r.guest_status} "
		      f"| pay={r.payment_status} paid={r.total_paid_amount:.0f} "
		      f"| {_doc_summary(r)}")
	return {"seeded": len(created), "failed": [c[0] for c in created if c[3]]}


def run_manual(commit=True):
	"""Seed the non-Guesty path: a reservation keyed in by hand, checked in and out.

	No `guesty_id` and no folio, so the invoice is built from the reservation's own
	amounts (`get_rate_card_items`) and settled in full at departure. Kept in the
	seed because this path has no Guesty payload to fall back on and is the one
	that silently produced no documents when the billing gate was applied to it.
	"""
	_preflight()
	prop = frappe.db.get_value("Property", {"guesty_id": LISTING}, "name")
	if not prop:
		print("\nSkipped manual scenario: no Property carries the demo listing id.")
		return {"seeded": 0}

	s = MANUAL_SEED
	existing = frappe.db.get_value("Reservation", {"email_id": s["email"]}, "name")
	if existing:
		print(f"\nManual scenario already seeded: {existing}")
		return {"seeded": 0}

	doc = frappe.new_doc("Reservation")
	doc.update({
		"property_id": prop,
		"reservation_type": "Booking",
		"reservation_status": "Confirmed",
		"first_name": s["first_name"],
		"last_name": s["last_name"],
		"email_id": s["email"],
		"phone_number": s["phone"],
		"reservation_check_in": s["check_in"],
		"reservation_check_out": s["check_out"],
		"no_of_nights": s["nights"],
		"no_of_adults": 2,
		"reservation_management_fee": s["fee"],
		"notes": s["label"],
	})
	doc.insert(ignore_permissions=True)

	# Same two saves a user makes in the form.
	for guest_status in ("Checkin", "Checkout"):
		doc.guest_status = guest_status
		doc.save(ignore_permissions=True)
		doc.reload()

	if commit:
		frappe.db.commit()

	print(f"\nSeeded 1 manual reservation:")
	print(f"  {s['idx']} {s['label']:44} -> {doc.name} | {doc.reservation_status}/{doc.guest_status} "
	      f"| pay={doc.payment_status} paid={doc.total_paid_amount:.0f} due={doc.outstanding_amount:.0f} "
	      f"| {_doc_summary(doc)}")
	return {"seeded": 1}


def run_items(commit=True):
	"""Seed the Item-routing scenarios and report which Item each charge hit.

	Prints one line per invoice item — code, name, group, and whether the Item was
	minted by this run or reused — plus a PASS/FAIL against the expected code, so
	a run is a readable check of the routing rules rather than a data dump.
	"""
	_preflight()
	_ensure_item_fixture()
	before = set(frappe.get_all("Item", pluck="name"))
	seen = set(before)  # grows as the run mints items, so a later reuse reads as reuse

	results = []
	for idx, label, guest_name, ci, co, charges, expected in _item_scenarios():
		money = _paid(extras=charges)
		for status in ("confirmed", "checkedin"):
			payload = _payload(idx, status, ci, co, 3, money, guest_name, "manual", label)
			sync_reservations.upsert_one(payload)
		if commit:
			frappe.db.commit()
		results.append((idx, label, expected, frappe.db.get_value(
			"Reservation", {"guesty_id": f"{PREFIX}RES-{idx}"}, "name")))

	print(f"\nSeeded {len(results)} item-routing reservations:")
	passed = 0
	for idx, label, expected, name in results:
		r = frappe.get_doc("Reservation", name)
		print(f"  {idx} {label:52} -> {name} | SI={r.sales_invoice or 'none'}")
		codes = []
		if r.sales_invoice:
			for item in frappe.get_doc("Sales Invoice", r.sales_invoice).items:
				group, item_name = frappe.db.get_value("Item", item.item_code, ["item_group", "item_name"])
				state = "NEW    " if item.item_code not in seen else "reused "
				seen.add(item.item_code)
				print(f"       {state} {item.item_code[:60]:60} | name={item_name[:40]:40} | {group}")
				codes.append(item.item_code)
		ok = all(code in codes for code in expected)
		passed += ok
		print(f"       expected {expected} -> {'PASS' if ok else 'FAIL'}")

	minted = set(frappe.get_all("Item", pluck="name")) - before
	print(f"\n  Items minted by this run: {sorted(minted) or 'none'}")
	print(f"  {passed}/{len(results)} scenarios matched their expected Item.")
	return {"seeded": len(results), "passed": passed, "minted": sorted(minted)}


def _ensure_item_fixture():
	"""An Item whose name matches a charge title but whose code does not — the
	case that must be reused rather than duplicated."""
	if frappe.db.exists("Item", ITEM_FIXTURE["item_code"]):
		return
	item = frappe.get_doc({
		"doctype": "Item",
		"item_code": ITEM_FIXTURE["item_code"],
		"item_name": ITEM_FIXTURE["item_name"],
		"item_group": "Services" if frappe.db.exists("Item Group", "Services") else "All Item Groups",
		"stock_uom": "Nos",
		"is_stock_item": 0,
		"is_sales_item": 1,
	})
	item.flags.ignore_permissions = True
	item.insert()


def _doc_summary(reservation):
	"""One-line SI / PE / CN state for a seeded reservation."""
	parts = []
	for label, doctype, name in (
		("SI", "Sales Invoice", reservation.sales_invoice),
		("PE", "Payment Entry", reservation.payment_entry),
		("CN", "Sales Invoice", reservation.credit_note),
		("RPE", "Payment Entry", reservation.refund_payment_entry),
	):
		if not name:
			continue
		doc = frappe.db.get_value(
			doctype, name, ["docstatus", "grand_total" if doctype == "Sales Invoice" else "paid_amount"],
			as_dict=True,
		)
		if not doc:
			continue
		state = {0: "draft", 1: "submitted", 2: "cancelled"}.get(doc.docstatus, "?")
		amount = doc.get("grand_total") if doctype == "Sales Invoice" else doc.get("paid_amount")
		parts.append(f"{label}={name} ({state}, {abs(amount or 0):.0f})")
	return " ".join(parts) or "no documents"


# ----------------------------------------------------------------------
# Cleanup
# ----------------------------------------------------------------------

def cleanup(customers=True):
	"""Delete the seed reservations and every document they raised.

	Order matters: Payment Entries reference invoices and Credit Notes reference
	the invoice they reverse, so payments are cancelled first, then returns, then
	the original invoices. The Reservation's link fields are cleared before the
	documents are deleted, otherwise the link check blocks the delete.
	"""
	# Guesty seeds carry the ZZSEED- id; the manual seed has none, so it is found
	# by its @zzseed.local guest instead.
	names = set(frappe.get_all("Reservation", filters={"guesty_id": ["like", f"{PREFIX}%"]}, pluck="name"))
	names.update(frappe.get_all("Reservation", filters={"email_id": ["like", "%@zzseed.local"]}, pluck="name"))
	docs = 0
	for name in names:
		docs += _teardown_documents(name)
		frappe.delete_doc("Reservation", name, force=True, ignore_permissions=True)

	frappe.db.commit()
	print(f"Deleted {len(names)} seed reservations and {docs} linked documents.")

	deleted_customers = _cleanup_customers() if customers else 0
	deleted_items = _cleanup_items()
	return {"deleted": len(names), "documents": docs,
	        "customers": deleted_customers, "items": deleted_items}


LINK_FIELDS = ("sales_invoice", "balance_sales_invoice", "credit_note",
               "payment_entry", "advance_payment_entry", "refund_payment_entry")


def _teardown_documents(reservation):
	"""Cancel + delete the invoices and payments raised for one reservation."""
	res = frappe.db.get_value("Reservation", reservation, LINK_FIELDS, as_dict=True) or {}

	# Collected from the back-link Custom Field as well as the Reservation's own
	# links: a Credit Note carries the back-link but is not always linked back.
	payments = set(frappe.get_all("Payment Entry", filters={"reservation": reservation}, pluck="name"))
	invoices = set(frappe.get_all("Sales Invoice", filters={"reservation": reservation}, pluck="name"))
	payments.update(filter(None, (res.get("payment_entry"), res.get("advance_payment_entry"),
	                              res.get("refund_payment_entry"))))
	invoices.update(filter(None, (res.get("sales_invoice"), res.get("balance_sales_invoice"),
	                              res.get("credit_note"))))

	# Break the links first — an existing link blocks the delete.
	for field in LINK_FIELDS:
		if res.get(field):
			frappe.db.set_value("Reservation", reservation, field, None, update_modified=False)

	count = 0
	for name in payments:
		count += _cancel_and_delete("Payment Entry", name)

	# Returns (credit notes) reference the invoice they reverse — cancel them first.
	ordered = sorted(invoices, key=lambda n: not frappe.db.get_value("Sales Invoice", n, "is_return"))
	for name in ordered:
		count += _cancel_and_delete("Sales Invoice", name)
	return count


def _cancel_and_delete(doctype, name):
	if not frappe.db.exists(doctype, name):
		return 0
	doc = frappe.get_doc(doctype, name)
	doc.flags.ignore_permissions = True
	try:
		if doc.docstatus == 1:
			doc.cancel()
		frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
	except Exception as e:
		print(f"  ! could not remove {doctype} {name}: {type(e).__name__}: {e}")
		return 0
	return 1


def _cleanup_items():
	"""Remove only the Items the item-routing scenarios minted.

	Those charge titles all start with ZZSEED, so the Item Master proper — including
	anything a seeded charge merely *reused* — is left untouched.
	"""
	names = frappe.get_all("Item", filters={"item_code": ["like", "ZZSEED%"]}, pluck="name")
	deleted = 0
	for name in names:
		try:
			frappe.delete_doc("Item", name, force=True, ignore_permissions=True)
			deleted += 1
		except Exception as e:
			print(f"  ! kept Item {name}: {type(e).__name__}: {e}")
	frappe.db.commit()
	print(f"Deleted {deleted} seed items.")
	return deleted


def _cleanup_customers():
	"""Seed guests are identifiable by their @zzseed.local address."""
	names = frappe.get_all("Customer", filters={"email_id": ["like", "%@zzseed.local"]}, pluck="name")
	deleted = 0
	for name in names:
		try:
			frappe.delete_doc("Customer", name, force=True, ignore_permissions=True)
			deleted += 1
		except Exception as e:
			print(f"  ! kept Customer {name}: {type(e).__name__}: {e}")
	frappe.db.commit()
	print(f"Deleted {deleted} seed customers.")
	return deleted
