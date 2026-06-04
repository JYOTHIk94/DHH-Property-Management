# Copyright (c) 2026, Jyothi and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import getdate, today, nowdate, flt


class Reservation(Document):

    def validate(self):
        self.validate_check_in_date()
        self.validate_check_out_date()
        self.calculate_booking_amount()
        self.calculate_total_amount()
        self.get_or_create_customer()

    def on_submit(self):
        self.create_sales_order()
        self.update_property_vacancy()

    def on_update_after_submit(self):
        self.calculate_total_amount()

        if self.workflow_state == "Checked Out":

            if not self.amount_paid_by_guest or not self.mode_of_payment:
                frappe.throw("Please enter the amount paid by guest and mode of payment before checking out.")

            if not self.sales_invoice:
                self.create_sales_invoice_and_payment()

            self.checkout_guest_count_update()

    def on_cancel(self):
        self.checkout_guest_count_update()

    def calculate_total_amount(self):
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

    def create_sales_order(self):
        if self.reservation_status != "Confirmed":
            return

        if self.sales_order:
            return

        if not self.property_id:
            frappe.throw("Property is required")

        property_doc = frappe.get_doc("Property", self.property_id)

        if not self.reservation_check_in or not self.reservation_check_out:
            frappe.throw("Check-in and Check-out dates are required")

        check_in = getdate(self.reservation_check_in)
        check_out = getdate(self.reservation_check_out)

        num_nights = (check_out - check_in).days

        if num_nights <= 0:
            frappe.throw("Check-out date must be after Check-in date")

        nightly_rate = property_doc.base_price_per_night or 0

        item_code = "Long Term Rental" if num_nights > 20 else "Short Term Rental"

        # ✅ Build items list properly
        items = [{
            "item_code": item_code,
            "qty": num_nights,
            "rate": nightly_rate,
        }]

        # ✅ Add service charge if exists
        if self.reservation_management_fee:
            items.append({
                "item_code": "Service Charge",
                "qty": 1,
                "rate": self.reservation_management_fee
            })

        # ✅ Create Sales Order
        sales_order = frappe.get_doc({
            "doctype": "Sales Order",
            "customer": self.guest,
            "transaction_date": today(),
            "delivery_date": self.reservation_check_in,
            "items": items
        })

        sales_order.insert(ignore_permissions=True)
        sales_order.submit()

        self.db_set("sales_order", sales_order.name)

        frappe.msgprint(f"Sales Order {sales_order.name} created successfully.")

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

    def create_sales_invoice_and_payment(self):
        if not self.sales_order:
            frappe.throw("Sales Order not found")

        if self.sales_invoice:
            return

        # Imported lazily so the module loads even where ERPNext isn't present yet
        # (e.g. Frappe Cloud build/validation before erpnext is installed).
        from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

        sales_invoice = make_sales_invoice(self.sales_order)

        sales_invoice.posting_date = nowdate()
        sales_invoice.set_posting_time = 1

        sales_invoice.flags.ignore_permissions = True
        sales_invoice.insert()
        sales_invoice.submit()

        self.db_set("sales_invoice", sales_invoice.name)

        paid_amount = flt(self.amount_paid_by_guest or 0)

        if self.mode_of_payment and paid_amount > 0:
            self.create_payment_entry(sales_invoice, self.mode_of_payment, paid_amount)

    def create_payment_entry(self, sales_invoice, mode_of_payment, paid_amount, reference_no=None):

        company = frappe.defaults.get_user_default("Company")

        pe = frappe.new_doc("Payment Entry")
        pe.payment_type = "Receive"
        pe.posting_date = nowdate()
        pe.party_type = "Customer"
        pe.party = sales_invoice.customer
        pe.party_name = frappe.db.get_value("Customer", sales_invoice.customer, "customer_name")

        pe.mode_of_payment = mode_of_payment
        pe.paid_amount = paid_amount
        pe.received_amount = paid_amount

        pe.paid_to = frappe.db.get_value(
            "Mode of Payment Account",
            {"parent": mode_of_payment, "company": company},
            "default_account"
        )

        if not pe.paid_to:
            frappe.throw(f"No default account for Mode of Payment '{mode_of_payment}' in company '{company}'")

        pe.paid_from_account_currency = frappe.db.get_value("Account", pe.paid_to, "account_currency")
        pe.received_currency = frappe.db.get_value("Customer", pe.party, "default_currency") or frappe.db.get_default("currency")

        if pe.paid_from_account_currency == pe.received_currency:
            pe.target_exchange_rate = 1
        else:
            pe.target_exchange_rate = frappe.db.get_value(
                "Currency Exchange",
                {
                    "from_currency": pe.paid_from_account_currency,
                    "to_currency": pe.received_currency
                },
                "exchange_rate"
            ) or 1

        pe.append("references", {
            "reference_doctype": "Sales Invoice",
            "reference_name": sales_invoice.name,
            "total_amount": sales_invoice.grand_total,
            "outstanding_amount": sales_invoice.outstanding_amount,
            "allocated_amount": paid_amount
        })

        pe.flags.ignore_permissions = True
        pe.insert()
        pe.submit()

        self.db_set("payment_entry", pe.name)
        frappe.msgprint(f"Payment Entry {pe.name} created successfully.")


    def update_property_vacancy(self):

        if not self.property_id:
            return

        # Get Property
        property_doc = frappe.get_doc("Property", self.property_id)

        # Try to find existing log
        log_name = frappe.db.get_value(
            "Property Vacancy Log",
            {"property": self.property_id},
            "name"
        )

        if log_name:
            log_doc = frappe.get_doc("Property Vacancy Log", log_name)
            log_doc.occupied_guests = log_doc.occupied_guests + self.no_of_adults + self.no_of_children

        else:
            log_doc = frappe.new_doc("Property Vacancy Log")
            log_doc.property = self.property_id
            log_doc.maximum_no_of_guests = property_doc.maximum_no_of_guests or 0
            log_doc.occupied_guests = (self.no_of_adults or 0) + (self.no_of_children or 0)
        log_doc.save(ignore_permissions=True)

    def checkout_guest_count_update(self):

        if not self.property_id:
            return

        guest_count = (self.no_of_adults or 0) + (self.no_of_children or 0)

        # ✅ Correct Doctype name
        log_name = frappe.db.get_value(
            "Property Vacancy Log",
            {"property": self.property_id},
            "name"
        )
        

        if not log_name:
            return  # No log exists → nothing to update

        log_doc = frappe.get_doc("Property Vacancy Log", log_name)

        # ✅ Reduce occupied guests
        log_doc.occupied_guests = (log_doc.occupied_guests or 0) - guest_count

        # ✅ Prevent negative values
        if log_doc.occupied_guests < 0:
            log_doc.occupied_guests = 0

        # ✅ Always update available
        log_doc.available = (
            (log_doc.maximum_no_of_guests or 0) -
            (log_doc.occupied_guests or 0)
        )

        log_doc.save(ignore_permissions=True)

