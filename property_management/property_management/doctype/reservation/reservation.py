# Copyright (c) 2026, Jyothi and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import getdate, today, nowdate, flt, cint


class Reservation(Document):

    def validate(self):
        self.set_default_company()
        self.validate_check_in_date()
        self.validate_check_out_date()
        self.calculate_booking_amount()
        self.calculate_total_amount()
        self.get_or_create_customer()

    def set_default_company(self):
        # Default the company from Property Settings when not set on the reservation.
        if not self.company:
            self.company = frappe.db.get_single_value("Property Settings", "default_company")

    def on_update(self):
        # Reservation is non-submittable. Lifecycle status (Draft/Reserved/
        # Confirmed/Cancelled) is separate from the guest *stay* status
        # (Not Arrived/Checkin/Checkout) — the latter drives billing.
        status = self.reservation_status
        status_changed = self.has_value_changed("reservation_status")
        guest_status = self.guest_status
        guest_status_changed = self.has_value_changed("guest_status")

        # Availability is date-range based: only overlapping bookings count against
        # capacity. Validate when confirmed or when the guest checks in.
        if (status == "Confirmed" and status_changed) or (
            guest_status == "Checkin" and guest_status_changed
        ):
            self.check_availability()

        # Check-in raises the advance invoice + payment; checkout the balance.
        if guest_status == "Checkin":
            self.process_checkin()
        elif guest_status == "Checkout":
            self.process_checkout()

        if status == "Cancelled" and status_changed:
            self.process_cancellation()

        self.update_status_fields()

    def check_availability(self):
        """Block only when *overlapping* bookings would exceed the property capacity.

        Two date ranges overlap when check_in < other.check_out AND
        check_out > other.check_in — so back-to-back / different ranges are allowed
        (e.g. Aug 2-6 and Aug 7-9 never conflict). Same/overlapping range is capped
        at the property's maximum_no_of_guests.
        """
        if not self.property_id or not self.reservation_check_in or not self.reservation_check_out:
            return

        max_guests = cint(frappe.db.get_value("Property", self.property_id, "maximum_no_of_guests"))
        this_guests = cint(self.no_of_adults) + cint(self.no_of_children)

        booked = frappe.db.sql(
            """
            SELECT COALESCE(SUM(no_of_adults + no_of_children), 0)
            FROM `tabReservation`
            WHERE property_id = %(property)s
              AND name != %(name)s
              AND reservation_status = 'Confirmed'
              AND reservation_check_in < %(check_out)s
              AND reservation_check_out > %(check_in)s
            """,
            {
                "property": self.property_id,
                "name": self.name or "",
                "check_in": self.reservation_check_in,
                "check_out": self.reservation_check_out,
            },
        )[0][0]

        if cint(booked) + this_guests > max_guests:
            frappe.throw(
                f"{self.property_id} is not available for {self.reservation_check_in} to "
                f"{self.reservation_check_out}. Capacity is {max_guests} guest(s); "
                f"{cint(booked)} already booked for overlapping dates."
            )

    def process_cancellation(self):
        """When the reservation is cancelled, cancel its linked Payment Entries
        first (they reference the invoices), then the Sales Invoices."""
        # Payment Entries must be cancelled before the invoices they pay.
        for pename in [self.payment_entry, self.advance_payment_entry]:
            if pename and frappe.db.exists("Payment Entry", pename):
                pe = frappe.get_doc("Payment Entry", pename)
                if pe.docstatus == 1:
                    pe.flags.ignore_permissions = True
                    pe.cancel()

        for siname in [self.balance_sales_invoice, self.sales_invoice]:
            if siname and frappe.db.exists("Sales Invoice", siname):
                si = frappe.get_doc("Sales Invoice", siname)
                if si.docstatus == 1:
                    si.flags.ignore_permissions = True
                    si.cancel()

    def process_checkin(self):
        """Check-in: submit an invoice + payment for the advance amount
        (captured in `reservation_sd`), allocated to that invoice.
        """
        advance = flt(self.reservation_sd or 0)
        if advance <= 0 or self.advance_payment_entry:
            return

        # The advance is applied to rental first, then to the service charge.
        rental_total = flt(self.reservation_item or 0)
        adv_rental = min(advance, rental_total)
        adv_service = advance - adv_rental

        sales_invoice = self.create_invoice(adv_rental, adv_service)
        self.db_set("sales_invoice", sales_invoice.name)

        pe = self.make_payment(sales_invoice, self.advance_mode_of_payment or "Cash", advance)
        if pe:
            self.db_set("advance_payment_entry", pe.name)

    def process_checkout(self):
        # If a balance is still outstanding (total - advance), raise ANOTHER
        # invoice + payment for that balance and allocate the payment to it.
        advance = flt(self.reservation_sd or 0)
        balance = flt(self.total_amount or 0) - advance

        if balance > 0 and not self.payment_entry:
            # Remaining rental + remaining service charge after the advance.
            rental_total = flt(self.reservation_item or 0)
            fee = flt(self.reservation_management_fee or 0)
            adv_rental = min(advance, rental_total)
            adv_service = advance - adv_rental
            bal_rental = rental_total - adv_rental
            bal_service = fee - adv_service

            sales_invoice = self.create_invoice(bal_rental, bal_service)
            self.db_set("balance_sales_invoice", sales_invoice.name)

            mode_of_payment = self.advance_mode_of_payment or "Cash"
            pe = self.make_payment(sales_invoice, mode_of_payment, balance)
            if pe:
                self.db_set("payment_entry", pe.name)

    def calculate_total_amount(self):
        # Guesty is authoritative for money on synced reservations — keep the
        # grand total mapped from the payload (it includes taxes).
        if self.flags.get("from_guesty"):
            return
        self.total_amount = (
            (self.reservation_item or 0) +
            (self.reservation_management_fee or 0)
        )

    def validate_check_in_date(self):
        # Reservations synced from Guesty may have past check-in dates (historical bookings).
        if self.flags.get("from_guesty"):
            return
        if self.reservation_check_in and getdate(self.reservation_check_in) < getdate(today()):
            frappe.throw("Check-in date must be today or a future date.")

    def validate_check_out_date(self):
        if self.reservation_check_out and getdate(self.reservation_check_out) < getdate(self.reservation_check_in):
            frappe.throw("Check-out date must be after the check-in date.")

    def calculate_booking_amount(self):
        # For Guesty-synced reservations, Guesty is authoritative for money — keep the
        # amount we mapped from the reservation instead of recomputing from the property.
        if self.flags.get("from_guesty"):
            return
        if not self.property_id:
            return

        property_doc = frappe.get_doc("Property", self.property_id)

        if not self.reservation_check_in or not self.reservation_check_out:
            return

        check_in = getdate(self.reservation_check_in)
        check_out = getdate(self.reservation_check_out)

        num_nights = (check_out - check_in).days

        if num_nights <= 0:
            return

        nightly_rate = property_doc.base_price_per_night or 0
        total_amount = num_nights * nightly_rate

        self.reservation_item = total_amount
        self.calculate_total_amount()

    # def create_sales_order(self):
    #     if self.reservation_status != "Confirmed":
    #         return
    #
    #     if self.sales_order:
    #         return
    #
    #     if not self.property_id:
    #         frappe.throw("Property is required")
    #
    #     property_doc = frappe.get_doc("Property", self.property_id)
    #
    #     if not self.reservation_check_in or not self.reservation_check_out:
    #         frappe.throw("Check-in and Check-out dates are required")
    #
    #     check_in = getdate(self.reservation_check_in)
    #     check_out = getdate(self.reservation_check_out)
    #
    #     num_nights = (check_out - check_in).days
    #
    #     if num_nights <= 0:
    #         frappe.throw("Check-out date must be after Check-in date")
    #
    #     nightly_rate = property_doc.base_price_per_night or 0
    #
    #     item_code = "Long Term Rental" if num_nights > 20 else "Short Term Rental"
    #
    #     # ✅ Build items list properly
    #     items = [{
    #         "item_code": item_code,
    #         "qty": num_nights,
    #         "rate": nightly_rate,
    #     }]
    #
    #     # ✅ Add service charge if exists
    #     if self.reservation_management_fee:
    #         items.append({
    #             "item_code": "Service Charge",
    #             "qty": 1,
    #             "rate": self.reservation_management_fee
    #         })
    #
    #     # ✅ Create Sales Order
    #     sales_order = frappe.get_doc({
    #         "doctype": "Sales Order",
    #         "customer": self.guest,
    #         "transaction_date": today(),
    #         "delivery_date": self.reservation_check_in,
    #         "items": items
    #     })
    #
    #     sales_order.insert(ignore_permissions=True)
    #     sales_order.submit()
    #
    #     self.db_set("sales_order", sales_order.name)
    #
    #     frappe.msgprint(f"Sales Order {sales_order.name} created successfully.")

    def get_or_create_customer(self):
        if self.guest:
            return self.guest

        customer_name = None

        if self.email_id:
            customer_name = frappe.db.get_value(
                "Customer",
                {"email_id": self.email_id},
                "name"
            )

        if not customer_name and self.phone_number:
            customer_name = frappe.db.get_value(
                "Customer",
                {"mobile_no": self.phone_number},
                "name"
            )

        if customer_name:
            self.guest = customer_name
            return customer_name

        customer_group = (
            frappe.db.get_single_value("Selling Settings", "customer_group")
            or frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
            or "Individual"
        )
        territory = (
            frappe.db.get_single_value("Selling Settings", "territory")
            or "All Territories"
        )

        customer = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": f"{self.first_name} {self.last_name or ''}".strip(),
            "customer_type": "Individual",
            "customer_group": customer_group,
            "territory": territory,
            "email_id": self.email_id,
            "mobile_no": self.phone_number
        })
        customer.insert(ignore_permissions=True)

        self.guest = customer.name
        return customer.name

    def create_invoice(self, rental_amount, service_amount=0):
        """Create + submit a Sales Invoice whose total equals rental + service.

        The rental portion uses the Short/Long Term Rental item; the management fee
        (when present) is itemised as a separate "Service item" line. Used per
        payment stage — the advance invoice at check-in and, if a balance remains,
        a second invoice at checkout.
        """
        if not self.guest:
            frappe.throw("Guest (Customer) is required")

        if not self.property_id:
            frappe.throw("Property is required")

        if not self.reservation_check_in or not self.reservation_check_out:
            frappe.throw("Check-in and Check-out dates are required")

        # Use the reservation's No of Nights; fall back to the date span if unset.
        num_nights = cint(self.no_of_nights) or (
            getdate(self.reservation_check_out) - getdate(self.reservation_check_in)
        ).days
        item_code = "Long Term Rental" if num_nights > 10 else "Short Term Rental"

        items = []
        if flt(rental_amount) > 0:
            items.append({"item_code": item_code, "qty": 1, "rate": flt(rental_amount)})
        if flt(service_amount) > 0:
            items.append({"item_code": "Service item", "qty": 1, "rate": flt(service_amount)})
        if not items:  # nothing to bill (both zero) — fall back to a rental line
            items.append({"item_code": item_code, "qty": 1, "rate": flt(rental_amount)})

        sales_invoice = frappe.get_doc({
            "doctype": "Sales Invoice",
            "company": self.get_company(),
            "customer": self.guest,
            "reservation": self.name,
            "posting_date": nowdate(),
            "set_posting_time": 1,
            "due_date": nowdate(),
            "items": items,
        })
        sales_invoice.flags.ignore_permissions = True
        sales_invoice.insert()
        sales_invoice.submit()
        return sales_invoice

    def get_company(self):
        """Leaf company that invoices this reservation.

        Uses the reservation's `company`, falling back to Property Settings
        `default_company`. Multi-company setup (DHH Group + child LLCs): transactions
        must post against a leaf company, never a group parent.
        """
        company = self.company or frappe.db.get_single_value("Property Settings", "default_company")
        if not company:
            frappe.throw("Set a Default Company in Property Settings before invoicing.")
        # if frappe.db.get_value("Company", company, "is_group"):
        #     frappe.throw(
        #         f"Company '{company}' is a group company; choose a leaf (child) company instead."
        #     )
        return company

    def make_payment(self, sales_invoice, mode_of_payment, amount):
        """Create + submit a Payment Entry allocated to the (submitted) invoice."""
        sales_invoice = frappe.get_doc("Sales Invoice", sales_invoice.name)
        outstanding = flt(sales_invoice.outstanding_amount)
        amount = flt(amount)

        if outstanding <= 0 or amount <= 0:
            return None

        company = sales_invoice.company or self.get_company()

        paid_to = frappe.db.get_value(
            "Mode of Payment Account",
            {"parent": mode_of_payment, "company": company},
            "default_account"
        )
        if not paid_to:
            frappe.throw(f"No default account for Mode of Payment '{mode_of_payment}' in company '{company}'")

        pe = frappe.new_doc("Payment Entry")
        pe.payment_type = "Receive"
        pe.company = company
        pe.reservation = self.name
        pe.posting_date = nowdate()
        pe.party_type = "Customer"
        pe.party = sales_invoice.customer
        pe.mode_of_payment = mode_of_payment
        pe.paid_to = paid_to
        pe.paid_amount = amount
        pe.received_amount = amount

        # Never allocate more than what is still outstanding.
        allocated = min(amount, outstanding)
        pe.append("references", {
            "reference_doctype": "Sales Invoice",
            "reference_name": sales_invoice.name,
            "total_amount": sales_invoice.grand_total,
            "outstanding_amount": outstanding,
            "allocated_amount": allocated,
        })

        pe.flags.ignore_permissions = True
        pe.insert()
        pe.submit()

        frappe.msgprint(f"Payment Entry {pe.name} created for {sales_invoice.name}.")
        return pe

    def update_status_fields(self):
        """Reservation-level outstanding + payment status across both stage invoices.

        outstanding = total - (advance payment + balance payment).
        """
        # Guesty is authoritative for money on synced reservations — the
        # payment_status / outstanding / total_paid mapped from the payload win.
        if self.flags.get("from_guesty"):
            return

        if self.reservation_status == "Cancelled":
            self.db_set("outstanding_amount", 0)
            self.db_set("total_paid_amount", 0)
            self.db_set("payment_status", "Cancelled")
            return

        total = flt(self.total_amount or 0)

        collected = 0
        for pename in [self.advance_payment_entry, self.payment_entry]:
            if not pename:
                continue
            pe = frappe.db.get_value("Payment Entry", pename, ["paid_amount", "docstatus"], as_dict=True)
            if pe and pe.docstatus == 1:  # count only submitted (not cancelled) payments
                collected += flt(pe.paid_amount)

        outstanding = total - collected
        self.db_set("outstanding_amount", outstanding)
        self.db_set("total_paid_amount", collected)

        if collected <= 0:
            status = "Not Paid"
        elif outstanding > 0:
            status = "Partially Paid"
        else:
            status = "Fully Paid"
        self.db_set("payment_status", status)

