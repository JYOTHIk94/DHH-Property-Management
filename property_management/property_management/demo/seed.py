# Copyright (c) 2026, Jyothi and contributors
# For license information, please see license.txt
"""Seed demo data for a Property Management test run.

Creates Property Types, Properties and (Draft) Reservations so the app can be
exercised end-to-end without a live Guesty connection. Everything is tagged so it
can be re-run idempotently and removed cleanly:

  - Properties:    property_name starts with "Demo "
  - Reservations:  reservation_link == DEMO_TAG

Reservations are left as **Draft** (not submitted), so the controller's
on_submit side effects (Sales Order / Sales Invoice / Vacancy Log) never fire —
this is record data for testing, not a financial flow.

Usage:
    bench --site dhh.com execute property_management.property_management.demo.seed.run
    bench --site dhh.com execute property_management.property_management.demo.seed.clear
"""

import frappe
from frappe.utils import add_days, today

DEMO_TAG = "DEMO-SEED"

PROPERTY_TYPES = ["Apartment", "Villa", "Studio", "Townhouse", "Penthouse"]

# property_name, property_type, status, bedrooms, area, max_guests, price/night
PROPERTIES = [
	("Demo Marina Apartment 1203", "Apartment", "Active", "2", 1100, 4, 450),
	("Demo Downtown Studio 0805", "Studio", "Active", "Studio", 550, 2, 300),
	("Demo Palm Villa 17", "Villa", "Active", "4", 4200, 8, 1500),
	("Demo JBR Penthouse 3001", "Penthouse", "Active", "3", 2800, 6, 1200),
	("Demo Business Bay Townhouse 7", "Townhouse", "Pending", "3", 2200, 6, 900),
	("Demo Deira Apartment 0410", "Apartment", "Inactive", "1", 700, 2, 250),
]

# property_name, check_in_offset, nights, adults, children, guest_type,
# first, last, email, phone, service_charge
RESERVATIONS = [
	("Demo Marina Apartment 1203", 5, 4, 2, 0, "Tourist", "John", "Carter",
	 "john.carter@example.com", "+971501112233", 0),
	("Demo Palm Villa 17", 10, 7, 6, 2, "Corporate", "Aisha", "Rahman",
	 "aisha.rahman@example.com", "+971502223344", 500),
	("Demo JBR Penthouse 3001", 3, 3, 4, 0, "Tourist", "Liu", "Wei",
	 "liu.wei@example.com", "+971503334455", 0),
	("Demo Downtown Studio 0805", 20, 7, 2, 0, "Tourist", "Maria", "Gomez",
	 "maria.gomez@example.com", "+971504445566", 150),
	("Demo Marina Apartment 1203", 30, 3, 3, 0, "Corporate", "Omar", "Khan",
	 "omar.khan@example.com", "+971505556677", 0),
]


def run():
	"""Create the demo dataset (clears any prior demo rows first)."""
	clear()

	pt = _seed_property_types()
	props = _seed_properties()
	res = _seed_reservations()

	frappe.db.commit()
	summary = {"property_types": pt, "properties": props, "reservations": res}
	print("Seed complete:", summary)
	return summary


def clear():
	"""Remove previously seeded demo data (reservations, then properties)."""
	res_names = frappe.get_all(
		"Reservation", filters={"reservation_link": DEMO_TAG}, pluck="name"
	)
	for name in res_names:
		doc = frappe.get_doc("Reservation", name)
		if doc.docstatus == 1:
			doc.cancel()
		frappe.delete_doc("Reservation", name, force=True, ignore_permissions=True)

	prop_names = frappe.get_all(
		"Property", filters={"property_name": ["like", "Demo %"]}, pluck="name"
	)
	for name in prop_names:
		# Drop any vacancy logs that point at this demo property first.
		for log in frappe.get_all(
			"Property Vacancy Log", filters={"property": name}, pluck="name"
		):
			frappe.delete_doc("Property Vacancy Log", log, force=True, ignore_permissions=True)
		frappe.delete_doc("Property", name, force=True, ignore_permissions=True)

	frappe.db.commit()
	print(f"Cleared {len(res_names)} reservation(s) and {len(prop_names)} property(ies).")


def _seed_property_types():
	count = 0
	for name in PROPERTY_TYPES:
		if not frappe.db.exists("Property Type", name):
			frappe.get_doc({"doctype": "Property Type", "property_type": name}).insert(
				ignore_permissions=True
			)
			count += 1
	return count


def _seed_properties():
	count = 0
	for name, ptype, status, bedrooms, area, max_guests, price in PROPERTIES:
		if frappe.db.exists("Property", name):
			continue
		frappe.get_doc({
			"doctype": "Property",
			"property_name": name,
			"property_type": ptype,
			"status": status,
			"date": today(),
			"bedrooms": bedrooms,
			"area": area,
			"maximum_no_of_guests": max_guests,
			"base_price_per_night": price,
		}).insert(ignore_permissions=True)
		count += 1
	return count


def _seed_reservations():
	count = 0
	for (prop, ci_off, nights, adults, children, gtype, first, last, email,
	     phone, service_charge) in RESERVATIONS:
		check_in = add_days(today(), ci_off)
		check_out = add_days(check_in, nights)
		doc = frappe.get_doc({
			"doctype": "Reservation",
			"property_id": prop,
			"reservation_type": "Booking",
			"reservation_status": "Draft",
			"reservation_booking_date": today(),
			"reservation_link": DEMO_TAG,
			"reservation_check_in": check_in,
			"reservation_check_out": check_out,
			"no_of_nights": nights,
			"guest_type": gtype,
			"no_of_adults": adults,
			"no_of_children": children,
			"first_name": first,
			"last_name": last,
			"email_id": email,
			"phone_number": phone,
			"reservation_management_fee": service_charge,
		})
		doc.insert(ignore_permissions=True)  # controller computes booking + total amount
		count += 1
	return count
