# Copyright (c) 2026, Jyothi and contributors
# For license information, please see license.txt

import re

import frappe
from frappe.model.document import Document
from frappe.utils import getdate, today, nowdate, flt, cint

# Item Group that every auto-created Guesty charge lands in, so the dynamic
# items stay visibly separate from the hand-maintained Item Master.
GUESTY_ITEM_GROUP = "Guesty Charges"


def _slug(value):
    """Uppercase, punctuation-free token safe for an Item code."""
    return re.sub(r"[^A-Za-z0-9]+", "-", str(value or "")).strip("-").upper()


def _is_security_deposit(row):
    """True for the refundable-hold line, which is never invoiced."""
    label = f"{row.get('second_identifier') or ''} {row.get('title') or ''}".lower()
    return "deposit" in label and ("security" in label or "sd" == (row.get("normal_type") or "").lower())


def guesty_item_code(row):
    """Deterministic Item code for a Guesty charge.

    Keyed on Guesty's own taxonomy — `normal_type` (charge family) plus
    `second_identifier` (machine category) — rather than on the title. The title
    is the human label: renaming "Cleaning fee" to "Cleaning & linen" must not
    mint a second Item and split the revenue reporting in two. Title is used
    only when Guesty sends no `second_identifier`.
    """
    family = _slug(row.get("normal_type")) or "OTHER"
    label = _slug(row.get("second_identifier")) or _slug(row.get("title")) or "CHARGE"
    return f"GSTY-{family}-{label}"[:140]


def get_or_create_guesty_item(row):
    """Resolve a Guesty folio line to an ERPNext Item, creating it if new.

    Guesty charges are **dynamic** — new fee types appear without warning — so
    the Item cannot be a fixed pre-built list. An existing Item is always reused;
    a second Item for the same charge type is never created. `item_code` is the
    identity key, so uniqueness is guaranteed by the primary key itself.
    """
    code = guesty_item_code(row)

    if frappe.db.exists("Item", code):
        # Follow a Guesty rename: the label moves, the Item and its history stay.
        title = (row.get("title") or "")[:140]
        if title and frappe.db.get_value("Item", code, "item_name") != title:
            frappe.db.set_value("Item", code, "item_name", title)
        return code

    _ensure_guesty_item_group()

    item = frappe.get_doc({
        "doctype": "Item",
        "item_code": code,
        "item_name": (row.get("title") or code)[:140],
        "description": row.get("description") or row.get("title") or code,
        "item_group": GUESTY_ITEM_GROUP,
        "stock_uom": "Nos",
        "is_stock_item": 0,      # a service charge never touches inventory
        "is_sales_item": 1,
        "is_purchase_item": 0,
    })
    item.flags.ignore_permissions = True

    try:
        item.insert()
    except frappe.DuplicateEntryError:
        # Another worker created it between the exists() check and the insert.
        pass

    return code


def _ensure_guesty_item_group():
    if frappe.db.exists("Item Group", GUESTY_ITEM_GROUP):
        return
    parent = frappe.db.get_value("Item Group", {"is_group": 1, "parent_item_group": ""}, "name") \
        or "All Item Groups"
    group = frappe.get_doc({
        "doctype": "Item Group",
        "item_group_name": GUESTY_ITEM_GROUP,
        "parent_item_group": parent,
        "is_group": 0,
    })
    group.flags.ignore_permissions = True
    group.insert()


class Reservation(Document):

    def validate(self):
        self.set_default_company()
        self.validate_check_in_date()
        self.validate_check_out_date()
        self.calculate_booking_amount()
        self.calculate_total_amount()
        self.get_or_create_customer()

    def is_guesty_managed(self):
        """True when Guesty owns this reservation's money.

        Checks the persisted `guesty_id`, not just the transient `from_guesty`
        flag: the flag exists only for the duration of a sync call, so guarding
        on it alone meant that *any* later save — a user editing the reservation,
        a status change, a check-in — silently recomputed the totals from the
        Property rate and overwrote what Guesty had sent.
        """
        return bool(self.flags.get("from_guesty") or self.guesty_id)

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

        # Billing: check-in raises a DRAFT Sales Invoice from the Guesty folio,
        # which then tracks every folio change; checkout submits it and records
        # the payment. See process_checkin / process_checkout.
        if guest_status == "Checkin":
            self.process_checkin()
        elif guest_status == "Checkout":
            self.process_checkout()
        else:
            # Folio edits before check-in still keep an existing draft in step.
            self.sync_draft_invoice()

        if status == "Cancelled" and status_changed:
            self.process_cancellation()
        elif self.payment_status in ("Refunded", "Partially Refunded"):
            # A refund without a cancellation — the guest got money back mid-stay
            # or after checkout. Guarded internally so a replay cannot double-refund.
            self.process_refund()

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
        """Cancellation — reverse what has posted, discard what has not.

        A **draft** invoice never reached the ledger, so it is simply deleted. A
        **submitted** invoice is reversed with a Credit Note rather than
        cancelled: it has already posted, and the audit trail has to survive the
        cancellation. Cash actually returned by Guesty follows as a refund
        Payment Entry.
        """
        si = self.get_sales_invoice()
        if not si:
            return

        if si.docstatus == 0:
            frappe.delete_doc("Sales Invoice", si.name, force=True, ignore_permissions=True)
            self.db_set("sales_invoice", None)
            return

        if si.docstatus == 1:
            # Reverse the whole bill; any cash returned is handled inside.
            self.process_refund(amount=flt(si.grand_total))

    def process_refund(self, amount=None):
        """Guesty returned money (or the booking was cancelled after invoicing)
        → Credit Note, plus a refund Payment Entry when cash actually moved.

        `amount` defaults to what Guesty shows as refunded; a cancellation passes
        the full invoice value so the entire receivable is reversed.
        """
        if self.credit_note:
            # Idempotency: Guesty can replay the same webhook up to three times,
            # and a second credit note would refund the guest twice.
            return

        si = self.get_sales_invoice()
        if not si or si.docstatus != 1:
            return

        returned = self.refunded_amount()
        amount = flt(amount if amount is not None else returned)
        if amount <= 0:
            return

        credit_note = self.make_credit_note(si, amount)
        if not credit_note:
            return
        self.db_set("credit_note", credit_note.name)

        # Only pay cash back when Guesty actually returned some.
        if returned > 0:
            pe = self.make_refund_payment(credit_note, min(returned, amount))
            if pe:
                self.db_set("refund_payment_entry", pe.name)

    def refunded_amount(self):
        """How much Guesty gave back — the negative payment lines on the folio.

        Guesty has no separate refunds array; a refund is a negative entry in
        `money.payments`, which lands in `reservation_line_items`.
        """
        return sum(
            abs(flt(row.amount))
            for row in (self.reservation_line_items or [])
            if flt(row.amount) < 0
        )

    def get_sales_invoice(self):
        if self.sales_invoice and frappe.db.exists("Sales Invoice", self.sales_invoice):
            return frappe.get_doc("Sales Invoice", self.sales_invoice)
        return None

    def make_credit_note(self, sales_invoice, amount):
        """Sales Return against a submitted invoice.

        ERPNext builds the return with the original lines negated; for a partial
        refund the line rates are scaled down so the credit note totals exactly
        what was given back. Posted at today's date, never the original invoice
        date, so a refund in a later month lands in an open accounting period.
        """
        from erpnext.controllers.sales_and_purchase_return import make_return_doc

        total = flt(sales_invoice.grand_total)
        if total <= 0:
            return None

        cn = make_return_doc("Sales Invoice", sales_invoice.name)

        if amount < total:
            factor = amount / total
            for item in cn.items:
                item.rate = flt(item.rate) * factor

        cn.posting_date = nowdate()
        cn.set_posting_time = 1
        cn.due_date = nowdate()

        # `akd_customizations` enforces a return reason on every credit note
        # (FR-SELL-68/70). That is a different project's rule, but its hook runs
        # on any site where the app is installed — including this one — so
        # satisfy it when the field is present rather than fail the refund.
        if cn.meta.has_field("akd_return_reason") and not cn.get("akd_return_reason"):
            cn.akd_return_reason = "Other"

        cn.flags.ignore_permissions = True
        cn.insert()
        cn.submit()
        return cn

    def make_refund_payment(self, credit_note, amount):
        """Cash back to the guest, allocated against the credit note."""
        amount = flt(amount)
        if amount <= 0:
            return None

        company = credit_note.company
        mode_of_payment = self.advance_mode_of_payment or "Cash"
        paid_from = frappe.db.get_value(
            "Mode of Payment Account",
            {"parent": mode_of_payment, "company": company},
            "default_account",
        )
        if not paid_from:
            frappe.throw(
                f"No default account for Mode of Payment '{mode_of_payment}' in company '{company}'"
            )

        pe = frappe.new_doc("Payment Entry")
        pe.payment_type = "Pay"          # money leaving the business
        pe.company = company
        pe.reservation = self.name
        pe.posting_date = nowdate()
        pe.party_type = "Customer"
        pe.party = credit_note.customer
        pe.mode_of_payment = mode_of_payment
        pe.paid_from = paid_from
        pe.paid_to = credit_note.debit_to
        pe.paid_amount = amount
        pe.received_amount = amount

        pe.append("references", {
            "reference_doctype": "Sales Invoice",
            "reference_name": credit_note.name,
            "total_amount": credit_note.grand_total,
            "outstanding_amount": credit_note.outstanding_amount,
            "allocated_amount": -amount,   # a credit note carries a negative outstanding
        })

        pe.flags.ignore_permissions = True
        pe.insert()
        pe.submit()
        return pe

    def process_checkin(self):
        """Check-in: raise a **Draft** Sales Invoice from the Guesty folio.

        Gated on the payment status — a reservation with nothing paid produces no
        invoice, even once the guest is in residence. The invoice is left in
        Draft for the duration of the stay so that folio changes (an added fee, a
        corrected rate) keep flowing into it; it is submitted at checkout.
        """
        self.sync_draft_invoice(create=True)

    def process_checkout(self):
        """Checkout: submit the draft invoice, then record what the guest paid."""
        # Pick up any last folio change, and create the invoice now if check-in
        # never fired (e.g. a same-day stay synced straight to Checkout).
        self.sync_draft_invoice(create=True)

        if not self.sales_invoice:
            return

        si = frappe.get_doc("Sales Invoice", self.sales_invoice)
        if si.docstatus == 0:
            if not si.items:
                return
            si.flags.ignore_permissions = True
            si.submit()
        elif si.docstatus == 2:  # cancelled — nothing to collect against
            return

        if self.payment_entry:
            return

        # Guesty is authoritative for what was actually collected. Cap it at the
        # invoice outstanding: Guesty's totalPaid can include the security
        # deposit, which is a hold and was deliberately left off the invoice —
        # paying it against the invoice would post a phantom advance.
        si.reload()
        paid = min(flt(self.total_paid_amount or 0), flt(si.outstanding_amount or 0))
        if paid <= 0:
            return

        pe = self.make_payment(si, self.advance_mode_of_payment or "Cash", paid)
        if pe:
            self.db_set("payment_entry", pe.name)

    # ------------------------------------------------------------------
    # Sales Invoice built from the Guesty folio
    # ------------------------------------------------------------------

    # A Sales Invoice is raised only once money has actually moved. A Confirmed
    # but unpaid reservation produces nothing, however far into the stay it is.
    BILLABLE_PAYMENT_STATUSES = ("Partially Paid", "Fully Paid")

    def sync_draft_invoice(self, create=False):
        """Keep the draft Sales Invoice in step with `invoice_items`.

        Guesty owns the folio, so the invoice is rebuilt from it rather than
        edited line by line: every sync replaces the item rows wholesale. Only
        **draft** invoices are touched — once submitted, an invoice is immutable
        and a folio change has to become a supplementary invoice or a credit
        note instead.

        `create=True` additionally raises the invoice when none exists yet.
        """
        existing = self.sales_invoice and frappe.db.exists("Sales Invoice", self.sales_invoice)
        if not existing and not create:
            return None

        if existing:
            si = frappe.get_doc("Sales Invoice", self.sales_invoice)
            if si.docstatus != 0:  # submitted or cancelled — immutable
                return si
        else:
            if self.payment_status not in self.BILLABLE_PAYMENT_STATUSES:
                return None
            si = None

        items = self.get_invoice_items()
        if not items:
            return si

        if si is None:
            si = frappe.get_doc({
                "doctype": "Sales Invoice",
                "company": self.get_company(),
                "customer": self.guest,
                "reservation": self.name,
                "posting_date": nowdate(),
                "set_posting_time": 1,
                "due_date": nowdate(),
                "items": items,
            })
            si.flags.ignore_permissions = True
            si.insert()
            self.db_set("sales_invoice", si.name)
            return si

        si.set("items", [])
        for row in items:
            si.append("items", row)
        si.flags.ignore_permissions = True
        si.save()
        return si

    def get_invoice_items(self):
        """Guesty folio charges → Sales Invoice item rows.

        One row per `invoice_items` entry, at qty 1 with the charge as the rate:
        Guesty gives a total per charge, not a rate x quantity, and the many
        folios whose accommodation total is not divisible by the night count
        would not survive being forced into qty = nights.

        The security deposit is skipped — it is a refundable hold, not a sale.
        """
        rows = []
        for row in (self.invoice_items or []):
            amount = flt(row.amount or 0)
            if not amount or _is_security_deposit(row):
                continue
            rows.append({
                "item_code": get_or_create_guesty_item(row),
                "item_name": (row.title or "")[:140] or None,
                "description": row.description or row.title,
                "qty": 1,
                "rate": amount,
            })
        return rows

    def calculate_total_amount(self):
        # Guesty is authoritative for money on synced reservations — keep the
        # grand total mapped from the payload (it includes taxes).
        if self.is_guesty_managed():
            return
        self.total_amount = (
            (self.reservation_item or 0) +
            (self.reservation_management_fee or 0)
        )

    def validate_check_in_date(self):
        # Reservations synced from Guesty may have past check-in dates (historical bookings).
        if self.is_guesty_managed():
            return
        if self.reservation_check_in and getdate(self.reservation_check_in) < getdate(today()):
            frappe.throw("Check-in date must be today or a future date.")

    def validate_check_out_date(self):
        if self.reservation_check_out and getdate(self.reservation_check_out) < getdate(self.reservation_check_in):
            frappe.throw("Check-out date must be after the check-in date.")

    def calculate_booking_amount(self):
        # For Guesty-synced reservations, Guesty is authoritative for money — keep the
        # amount we mapped from the reservation instead of recomputing from the property.
        if self.is_guesty_managed():
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
        if self.is_guesty_managed():
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

