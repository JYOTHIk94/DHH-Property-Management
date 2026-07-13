# Changelog

All notable changes to the **Property Management** app are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(`MAJOR.MINOR.PATCH`): breaking changes bump MAJOR, backward-compatible features
bump MINOR, and fixes bump PATCH.

## [Unreleased]

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
- Seed/demo helpers in `demo.py` (`seed_reservations`, `seed_availability_demo`).

### Changed
- **Reservation is now non-submittable** (removed submit/cancel/amend, docstatus
  lifecycle, and `amended_from`). Cancellation is a status value.
- Cancelling a reservation now **cancels its linked Payment Entries and Sales
  Invoices** (payments first, then invoices).
- `phone_number` changed to a plain **Data** field and made non-mandatory.
- `advance_mode_of_payment` defaults to **Cash**; the Payment Entry falls back to
  Cash when no mode is set.

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
