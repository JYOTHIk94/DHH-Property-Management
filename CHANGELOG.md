# Changelog

All notable changes to the **Property Management** app are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(`MAJOR.MINOR.PATCH`): breaking changes bump MAJOR, backward-compatible features
bump MINOR, and fixes bump PATCH.

## [Unreleased]

2026-07-21

Invoicing rebuilt around the **Guesty folio**: one draft invoice per stay built
from the folio charges, dynamic Items for Guesty's fee types, and credit-note
based refunds/cancellations. Plus channel normalisation and status-vocabulary
alignment with Guesty.

### Added
- **Folio-driven Sales Invoice.** Check-in raises a **draft** Sales Invoice built
  from the reservation's `invoice_items` (one line per Guesty charge, qty 1 with
  the charge as the rate). The draft is rebuilt on every sync for the duration of
  the stay, so added fees and corrected rates keep flowing in; checkout submits it
  and records the payment. Invoicing is gated on `payment_status` — a Confirmed
  but unpaid reservation raises nothing, however far into the stay it is. The
  security deposit is skipped: it is a refundable hold, not a sale.
- **Dynamic Items for Guesty charges** (`get_or_create_guesty_item`). Guesty fee
  types appear without warning, so Items are created on demand under a
  **Guesty Charges** Item Group as non-stock sales items. The item code
  (`GSTY-<normalType>-<secondIdentifier>`) is keyed on Guesty's taxonomy, not the
  title, so renaming a charge moves the label onto the existing Item instead of
  minting a second one and splitting revenue reporting.
- **Refunds and paid cancellations** (`process_refund`). Money returned by Guesty
  raises a **Credit Note** (Sales Return) against the submitted invoice plus a
  refund **Payment Entry** for cash actually returned. Partial refunds scale the
  return line rates so the credit note totals exactly what was given back; both
  documents post at today's date so a late refund lands in an open period. Guarded
  by the `credit_note` link, so a replayed Guesty webhook cannot double-refund.
  The refunded amount is derived from the negative payment lines on the folio
  (Guesty has no separate refunds array).
- **`Reservation Invoice Items` child table** and an *Invoice Items* section on
  the Reservation — the guest folio charges from Guesty `money.invoiceItems`
  (title, description, amount, currency, `normal_type`, `second_identifier`).
  This is *what the guest is charged*, as distinct from `reservation_line_items`
  (*what was actually paid*); the two do not correspond 1:1.
- **`channel_type` (Direct / OTA)** on the Reservation, and a `CHANNEL_MAP` that
  normalises Guesty's machine identifiers (`airbnb2`, `bookingCom`, `homeaway2`)
  to display names (Airbnb, Booking.com, Vrbo). Only `manual`/empty is treated as
  Direct; an unrecognised identifier is title-cased and classed as OTA rather than
  discarded.
- **Payment status labels** matching Guesty's UI (`SUCCEEDED` → *Approved*,
  `PARTIALLY_REFUNDED` → *Partially Refunded*, …) on the folio payment rows, with
  a title-case fallback for unmapped enums, so users can reconcile against Guesty.
- **Payment `description`** on `reservation_line_items` — Guesty's name for what
  the payment covered (display only, never a join key).
- Guesty booking/guest fields carried on the Reservation: **Confirmation Code**,
  **Guest Status**, **Related Reservation** (sibling of a split mid-stay move),
  **Credit Note** and **Refund Payment Entry**.

### Changed
- **`reservation_status` vocabulary** is now Draft / **Reserved** / **Awaiting
  Payment** / Confirmed / Cancelled (was Draft / Scheduled / …); Guesty
  `reserved`+`pending` map to Reserved and `awaiting_payment` to Awaiting Payment.
- **`payment_status` labels** renamed to **Not Paid / Partially Paid / Fully
  Paid** (was Unpaid / Partly Paid / Paid), matching Guesty's wording.
- **Payments tab reorganised:** `reservation_line_items` relabelled *Payment
  Details* and moved under the Payments section next to the new Invoice Items
  table; `source` relabelled **Channel**.
- **Guesty ownership is now decided by the persisted `guesty_id`**
  (`is_guesty_managed`), not only the transient `from_guesty` sync flag.
- Seed/demo data (`seed_demo.py`) now emits Guesty-shaped invoice items
  (`normalType` / `secondIdentifier` / description), payout economics, payment
  descriptions, `isFullyPaid`, and a spread of channels, so it exercises the real
  mappers instead of a reduced form of them.

### Fixed
- **Guesty totals were overwritten on any later save.** The authoritative-money
  guard checked only the `from_guesty` flag, which lives just for the duration of
  a sync call — so a user edit, a status change or a check-in silently recomputed
  the total from the Property rate. It now checks `guesty_id`.
- **Guest status stuck at "Not Arrived":** the stay status is read from
  `guestStay.status` (falling back to the lifecycle status for older payloads),
  matched underscore-insensitively so `checked_in` and `checkedin` both land.
- **Payout section permanently empty and `isFullyPaid` never applied:** Guesty
  returns only the fields the request asks for, and `money.channelCommission`,
  `channelCommissionTax`, `hostCommissionIncTax`, `payout` and `isFullyPaid` were
  missing from the `fields` string, arriving as `None` and being written as 0.
- **Fully-paid detection** now uses Guesty's explicit `isFullyPaid`, with
  `balanceDue` / `totalPaid` as the fallback for older payloads.
- **Phantom advance at checkout:** the payment is capped at the invoice
  outstanding, because Guesty's `totalPaid` can include the security deposit —
  a hold that is deliberately left off the invoice.
- Cancellation of a **draft** invoice now deletes it (it never reached the
  ledger); a **submitted** invoice is reversed with a credit note rather than
  cancelled, so the audit trail survives.

### Removed
- **Two-stage invoicing** (`create_invoice`): the advance invoice + Payment Entry
  at check-in and the balance invoice + Payment Entry at checkout, along with the
  rental/service split of each stage. One folio invoice per stay replaces it.
- **`folio_payments`** (Reservation Payment) and **`scheduled_payments`** tables
  from the Reservation — payments are now carried by *Payment Details*.
- **`guesty_item_id` / `guesty_payment_id`** from Accommodation Fare, Reservation
  Line Items and Reservation Payment: these child rows are rebuilt wholesale on
  every sync, so nothing ever matched against them. Guesty line identity belongs
  on the Sales Invoice Item and Payment Entry instead.

2026-07-13

Major rework of the **Reservation → invoicing → payment** flow, multi-company
support, and date-based availability.

### Added
- **Reservation payment lifecycle** driven entirely by `reservation_status`
  (Draft → Confirmed → Checked In → Checked Out / Cancelled) via `on_update`.
- **Two-stage invoicing:** at check-in an invoice + Payment Entry is raised for
  the advance (`reservation_sd`, "Advance Payment"); at checkout a second invoice
  + Payment Entry is raised for the remaining balance. Both invoices are submitted
  and the payments are **allocated (linked)** to them.
- **Service item line:** the management fee (`reservation_management_fee`) is
  itemised as a separate "Service item" line on the invoice — each stage's amount
  is split into its rental and service portions (advance applied to rental first),
  so the fee is billed exactly once across the two invoices.
- **`company` on Reservation** — the leaf company that invoices the booking.
  Defaults from **Property Settings → Default Company**; guarded against group
  companies. Used for both Sales Invoice creation and Mode of Payment account
  selection. A `company` field was also added on **Property**.
- **`total_paid_amount`** field on Reservation (visible after checkout), plus
  `outstanding_amount` and `payment_status` synced from the submitted payments.
- **Connections dashboard:** `reservation` back-link Custom Field on **Sales
  Invoice** and **Payment Entry** (shipped as fixtures) so all stage invoices and
  payments appear under the Reservation's Connections.
- **Date-range availability check** (`check_availability`): a booking is blocked
  only when *overlapping* reservations would exceed the property's guest capacity.
  Different or back-to-back date ranges are always allowed.
- **Guesty guest folio mirrored on the Reservation** — a "Guesty Folio" section
  with the money breakdown (currency, total price, balance due, host payout,
  cleaning fee, taxes, fees) plus three child tables: **Invoice Items** (folio
  breakdown by line item), **Night Rates** (folio breakdown by night — from
  Guesty `nightlyRates`, else the accommodation fare split evenly across the
  stay), and **Payments** (`Reservation Invoice Item` / `Reservation Night Rate` /
  `Reservation Payment` doctypes). Populated from the Guesty reservation `money`
  object by the sync and webhook. ISO timestamps are normalised; unknown
  currencies are skipped.
- **Payout section** on the Reservation mirroring Guesty's payout view: Payout,
  Owner's Revenue, Your Commission, Net Income, Your Commission inc. Tax, Channel
  Commission, Channel Commission Tax — mapped from the Guesty `money` object.
- Additional Guesty booking/guest fields: **Source** (channel), **Check-in /
  Check-out Time** (planned arrival/departure), **No of Infants**, and **Notes**.
- Reservation form reorganised into **tabs** (Booking Details, Guests, Payments,
  Guest Folio & Invoice) to mirror the Guesty reservation layout.
- Seed/demo helpers in `demo.py` (`seed_reservations`, `seed_availability_demo`).

### Changed
- **Reservation is now non-submittable** (removed submit/cancel/amend, docstatus
  lifecycle, and `amended_from`). Cancellation is a status value.
- Cancelling a reservation now **cancels its linked Payment Entries and Sales
  Invoices** (payments first, then invoices).
- `phone_number` changed to a plain **Data** field and made non-mandatory.
- `advance_mode_of_payment` defaults to **Cash**; the Payment Entry falls back to
  Cash when no mode is set.
- Rental item threshold: **Long Term Rental** now applies for stays of **more than
  10 nights** (was 20); 10 nights or fewer use **Short Term Rental**.
- Rental item selection now uses the reservation's **`no_of_nights`** field (not
  the check-in/check-out date span), falling back to the date span when it is
  unset — so the item matches the recorded nights.

### Fixed
- **Availability false-negative:** availability was a perpetual `occupied_guests`
  counter per property that ignored dates, so non-overlapping bookings (e.g.
  Aug 2–6 and Aug 7–9) wrongly reported "property not available" at check-in. It
  is now computed from overlapping date ranges against capacity.
- `AttributeError` on checkout from a stale `workflow_state` reference (now uses
  `reservation_status`) and a removed `mode_of_payment` field.
- Delivery Note requirement (AKD `FR-SELL-26`) no longer blocks non-stock rental
  / service items on reservation invoices.

### Removed
- Sales Order creation from the reservation flow (only Sales Invoice + Payment
  Entry are used now).
- The counter-based `Property Vacancy Log` occupancy logic
  (`update_property_vacancy` / `checkout_guest_count_update`).

## [0.0.1] - 2026-04

### Added
- Initial Property Management app: **Property**, **Reservation**, **Property
  Type**, **Property Vacancy Log**, **Guesty Settings** doctypes.
- **Guesty integration:** listing → Property and reservation → Reservation sync
  (paginated, upsert on `guesty_id`, read-only toward Guesty), on-demand listing
  fetch, and webhook-driven real-time updates.

[Unreleased]: https://example.com/property_management/compare/v0.1.0...HEAD
[0.1.0]: https://example.com/property_management/releases/tag/v0.1.0
[0.0.1]: https://example.com/property_management/releases/tag/v0.0.1

