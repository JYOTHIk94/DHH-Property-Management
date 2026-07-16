"""Seed/demo data for testing the Reservation payment functionality.

Run with:
    bench --site dhh.com execute property_management.demo.seed_reservations

Each reservation is non-submittable. The flow is driven by `reservation_status`:
  Confirmed -> Checked In (advance -> draft SI + draft PE) -> Checked Out
  (SI + PE updated to the full amount; both remain in Draft).
"""

import frappe
from frappe.utils import add_days, today, flt


# (property, nights, first, last, advance_ratio, final_status)
SCENARIOS = [
    ("Mountain Views 4BR Villa w/ Assistant Room & Pool", 5, "Full", "PaidAtCheckin", 1.0, "Checked Out"),
    ("Test_ Ratione aut dolor", 5, "Partial", "ThenBalance", 0.4, "Checked Out"),
    ("Elegant 3BR, Skyline Retreat in Downtown Dubai", 4, "NoAdvance", "Confirmed", 0.0, "Confirmed"),
    ("Demo Palm Villa 17", 3, "MidStay", "PartialOnly", 0.5, "Checked In"),
]


def seed_reservations():
    results = []

    for idx, (prop, nights, first, last, advance_ratio, final_status) in enumerate(SCENARIOS):
        if not frappe.db.exists("Property", prop):
            results.append({"scenario": f"{first} {last}", "skipped": f"missing property {prop}"})
            continue

        r = frappe.new_doc("Reservation")
        r.property_id = prop
        r.reservation_type = "Booking"
        r.reservation_booking_date = today()
        r.reservation_check_in = add_days(today(), 2)
        r.reservation_check_out = add_days(today(), 2 + nights)
        r.first_name = first
        r.last_name = last
        r.email_id = f"{first.lower()}.{last.lower()}@seed-test.local"
        r.phone_number = f"+97150000{idx:04d}"
        r.no_of_adults = 2
        r.no_of_children = 0
        r.guest_type = "Tourist"
        r.reservation_status = "Confirmed"
        r.flags.ignore_permissions = True
        r.insert()

        total = flt(r.total_amount)
        advance = round(total * advance_ratio, 2)

        # Record the advance, then walk the status forward to the final state.
        if advance > 0:
            r.reservation_sd = advance
            r.advance_mode_of_payment = "Cash"

        if final_status in ("Checked In", "Checked Out"):
            r.guest_status = "Checkin"
            r.save(ignore_permissions=True)

        if final_status == "Checked Out":
            r.mode_of_payment = "Cash"
            r.guest_status = "Checkout"
            r.save(ignore_permissions=True)

        r.reload()
        results.append({
            "reservation": r.name,
            "scenario": f"{first} {last}",
            "status": r.reservation_status,
            "total": total,
            "advance": advance,
            "sales_invoice": r.sales_invoice,
            "payment_entry": r.payment_entry,
            "outstanding": r.outstanding_amount,
        })

    frappe.db.commit()

    for row in results:
        print(row)

    return results
