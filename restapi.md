# Property Management — REST API

## Create a Property

Frappe automatically exposes every DocType through its generic REST resource API, so
no custom code is needed to create a Property.

### Endpoint

```
POST /api/resource/Property
```

For the AKD site:

```
POST https://akd.com/api/resource/Property
```

### Headers

```
Authorization: token <api_key>:<api_secret>
Content-Type: application/json
```

### Request body

`property_name` is the only required field — it becomes the record name and must be unique.
All other fields are optional.

```json
{
  "property_name": "Marina Tower 1204",
  "property_type": "Apartment",
  "status": "Active",
  "bedrooms": "2",
  "area": 1250.5,
  "maximum_no_of_guests": 4,
  "base_price_per_night": 450,
  "date": "2026-06-01",
  "opportunity": "OPP-2026-00001",
  "property_onboarding_request": "POR-2026-00001"
}
```

### Full request body (with utility fields)

```json
{
  "property_name": "Marina Tower 1204",
  "property_type": "Apartment",
  "status": "Active",
  "date": "2026-06-01",
  "bedrooms": "2",
  "area": 1250.5,
  "maximum_no_of_guests": 4,
  "base_price_per_night": 450,

  "premise_number_dewa": "PRM-001",
  "account_number_dewa": "1234567890",
  "username_dewa": "akd_dewa",
  "password_dewa": "******",
  "registered_email_dewa": "billing@akd.com",
  "registered_phone_dewa": "+97150xxxxxxx",
  "registered_name_dewa": "AKD Consulting LLC",
  "paid_by_dewa": "Owner",
  "status_dewa": "Active",
  "dewa_bill_issue_date": "2026-05-01",
  "notes_dewa": "",

  "provider_internettv": "du",
  "account_number_internettv": "INT-001",
  "username_internettv": "akd_du",
  "password_internettv": "******",
  "registered_email_internettv": "billing@akd.com",
  "registered_phone_internettv": "+97150xxxxxxx",
  "registered_name_internettv": "AKD Consulting LLC",
  "wifi_router_username_internettv": "admin",
  "wifi_router_password_internettv": "******",
  "landline_number_internettv": "+9714xxxxxxx",
  "paid_by_internettv": "Owner",
  "status_internettv": "Active",
  "internet_bill_issue_date": "2026-05-01",
  "notes_internet": "",

  "provider_chiller": "Empower",
  "account_number_chiller": "CHL-001",
  "username_chiller": "akd_chiller",
  "password_chiller": "******",
  "registered_email_chiller": "billing@akd.com",
  "registered_phone_chiller": "+97150xxxxxxx",
  "registered_name_chiller": "AKD Consulting LLC",
  "owner_email_chiller": "owner@akd.com",
  "paid_by_chiller": "Owner",
  "status_chiller": "Active",
  "chiller_bill_issue_date": "2026-05-01",
  "notes_chiller": "",

  "type_gas": "Piped",
  "provider_name_gas": "Emirates Gas",
  "provider_phone_gas": "+9714xxxxxxx",
  "contract_no": "GAS-001",
  "username_gas": "akd_gas",
  "password_gas": "******",
  "registered_email_gas": "billing@akd.com",
  "registered_phone_gas": "+97150xxxxxxx",
  "registered_name_gas": "AKD Consulting LLC",
  "paid_by_gas": "Owner",
  "status_gas": "Active",
  "gas_bill_issue_date": "2026-05-01",
  "notes_gas": "",

  "payment_status_service_charge": "Paid",
  "service_charge_bill_issue_date": "2026-05-01",
  "landlord_received_a_service_charge_invoice": "Yes",
  "day_of_the_month_service_charge_billing": "1",
  "service_charge_attachments": "",
  "was_the_property_handed_over_in_the_last_3_months": "No",
  "notes_service_charge": ""
}
```

### Field reference

| Field | Type | Notes |
|-------|------|-------|
| `property_name` | Data | **Required**, unique — becomes the record name |
| `property_type` | Link → Property Type | Must reference an existing Property Type |
| `status` | Select | `Pending` (default) / `Active` / `Inactive` |
| `date` | Date | Defaults to today if omitted |
| `bedrooms` | Data | |
| `area` | Float | |
| `maximum_no_of_guests` | Int | |
| `base_price_per_night` | Currency | |
| `opportunity` | Link → Opportunity | |
| `property_onboarding_request` | Link → Property Onboarding Request | |
| DEWA / Internet-TV / Chiller / Gas / Service Charge | Data / Date | Optional utility fields (see full body above) |

> Permission: only roles with **create** on Property (by default **System Manager**) can use this endpoint.

### curl

```bash
curl -X POST 'https://akd.com/api/resource/Property' \
  -H 'Authorization: token <api_key>:<api_secret>' \
  -H 'Content-Type: application/json' \
  -d '{
    "property_name": "Marina Tower 1204",
    "property_type": "Apartment",
    "status": "Active",
    "area": 1250.5,
    "base_price_per_night": 450
  }'
```

### Success response (HTTP 200)

```json
{
  "data": {
    "name": "Marina Tower 1204",
    "property_name": "Marina Tower 1204",
    "property_type": "Apartment",
    "status": "Active",
    "date": "2026-06-01",
    "area": 1250.5,
    "base_price_per_night": 450,
    "doctype": "Property",
    "owner": "Administrator",
    "creation": "2026-06-01 10:00:00.000000",
    "modified": "2026-06-01 10:00:00.000000"
  }
}
```

### Common errors

| Status | Reason |
|--------|--------|
| 401 | Missing/invalid `Authorization` token |
| 403 | User lacks **create** permission on Property |
| 409 / 417 | A Property with the same `property_name` already exists (unique) |
| 417 | `property_type` references a non-existent Property Type |

---

## Get Property Type(s)

`Property Type` is a simple DocType: a single unique `Data` field `property_type`,
which is also the record name. It is exposed through the built-in REST resource API.

### Headers (all GET requests)

```
Authorization: token <api_key>:<api_secret>
```

### 1. List all Property Types

```
GET /api/resource/Property Type
```

For the AKD site (URL-encode the space as `%20`):

```
GET https://akd.com/api/resource/Property%20Type
```

Response (HTTP 200):

```json
{
  "data": [
    { "name": "Apartment" },
    { "name": "Villa" },
    { "name": "Studio" }
  ]
}
```

### 2. List with all fields

```
GET /api/resource/Property Type?fields=["name","property_type"]
```

```json
{
  "data": [
    { "name": "Apartment", "property_type": "Apartment" },
    { "name": "Villa", "property_type": "Villa" }
  ]
}
```

### 3. Get a single Property Type

```
GET /api/resource/Property Type/Apartment
```

```json
{
  "data": {
    "name": "Apartment",
    "property_type": "Apartment",
    "doctype": "Property Type",
    "owner": "Administrator",
    "creation": "2026-04-13 10:49:40.184748",
    "modified": "2026-04-13 10:50:17.260060"
  }
}
```

### 4. Filter / paginate

```
GET /api/resource/Property Type?filters=[["property_type","like","%apartment%"]]&limit_page_length=20&limit_start=0
```

| Query param | Purpose |
|-------------|---------|
| `fields` | JSON array of fields to return, e.g. `["name","property_type"]` |
| `filters` | JSON array of conditions, e.g. `[["property_type","like","%villa%"]]` |
| `limit_page_length` | Page size (use `0` for no limit) |
| `limit_start` | Offset for pagination |
| `order_by` | e.g. `property_type asc` |

### curl

```bash
# List all
curl -X GET 'https://akd.com/api/resource/Property%20Type' \
  -H 'Authorization: token <api_key>:<api_secret>'

# Single record
curl -X GET 'https://akd.com/api/resource/Property%20Type/Apartment' \
  -H 'Authorization: token <api_key>:<api_secret>'
```

> Permission: only roles with **read** on Property Type (by default **System Manager**) can use these endpoints.

---

## Create a Reservation

`Reservation` is a **submittable** DocType (auto-named `RES-######`). Creating via the
REST resource API inserts it as a **Draft** (`docstatus = 0`). Several fields are required,
and the controller runs validations and auto-calculations on save.

### Endpoint

```
POST /api/resource/Reservation
```

For the AKD site:

```
POST https://akd.com/api/resource/Reservation
```

### Headers

```
Authorization: token <api_key>:<api_secret>
Content-Type: application/json
```

### Required fields

| Field | Type | Notes |
|-------|------|-------|
| `property_id` | Link → Property | Must reference an existing Property |
| `reservation_check_in` | Date | Must be **today or a future date** |
| `reservation_check_out` | Date | Must be **after** check-in |
| `first_name` | Data | Guest first name |
| `phone_number` | Phone | Guest phone |

### Request body

```json
{
  "property_id": "Marina Tower 1204",
  "reservation_booking_date": "2026-06-01",
  "reservation_type": "Booking",
  "reservation_status": "Draft",
  "reservation_check_in": "2026-06-10",
  "reservation_check_out": "2026-06-14",
  "no_of_nights": 4,
  "reservation_manager": "vivek@quarkcyber.systems",
  "reservation_link": "https://booking.example.com/abc",

  "first_name": "John",
  "last_name": "Smith",
  "email_id": "john.smith@example.com",
  "phone_number": "+971501234567",
  "guest_type": "Tourist",
  "no_of_adults": 2,
  "no_of_children": 1,

  "reservation_management_fee": 150,
  "reservation_sd": 500,
  "mode_of_payment": "Cash",
  "amount_paid_by_guest": 0
}
```

### Select field options

| Field | Allowed values |
|-------|----------------|
| `reservation_type` | `Booking` / `Inquiry` / `Blocked` |
| `reservation_status` | `Draft` / `Confirmed` / `Checked In` / `Checked Out` / `Cancelled` |
| `guest_type` | `Tourist` / `Corporate` / `Owner` |

### Auto-managed fields — do NOT send these

The controller sets these; sending them is ignored or overwritten:

- `reservation_item` (Booking Amount) — calculated as `nights × Property.base_price_per_night`
- `total_amount` — `reservation_item + reservation_management_fee`
- `guest` — Customer is auto-found (by email/phone) or auto-created from the guest details
- `sales_order`, `sales_invoice`, `payment_entry` — created on submit / checkout
- `payment_status` — fetched from the linked Sales Invoice
- `amended_from` — set only when amending a cancelled reservation

### curl

```bash
curl -X POST 'https://akd.com/api/resource/Reservation' \
  -H 'Authorization: token <api_key>:<api_secret>' \
  -H 'Content-Type: application/json' \
  -d '{
    "property_id": "Marina Tower 1204",
    "reservation_check_in": "2026-06-10",
    "reservation_check_out": "2026-06-14",
    "first_name": "John",
    "last_name": "Smith",
    "phone_number": "+971501234567",
    "email_id": "john.smith@example.com",
    "no_of_adults": 2,
    "guest_type": "Tourist"
  }'
```

### Success response (HTTP 200) — created as Draft

```json
{
  "data": {
    "name": "RES-000001",
    "docstatus": 0,
    "property_id": "Marina Tower 1204",
    "reservation_check_in": "2026-06-10",
    "reservation_check_out": "2026-06-14",
    "no_of_nights": 4,
    "first_name": "John",
    "last_name": "Smith",
    "phone_number": "+971501234567",
    "guest": "John Smith",
    "reservation_item": 1800,
    "total_amount": 1950,
    "doctype": "Reservation"
  }
}
```

### Submitting the reservation

The POST above creates a **Draft**. Submitting (`docstatus = 1`) triggers the controller's
`on_submit` — and if `reservation_status` is `Confirmed`, it creates a Sales Order. Submit
with a follow-up call:

```
PUT /api/resource/Reservation/RES-000001
Content-Type: application/json

{ "docstatus": 1 }
```

### Common errors

| Status | Reason |
|--------|--------|
| 401 | Missing/invalid `Authorization` token |
| 403 | User lacks **create** permission on Reservation (allowed roles: System Manager, Receptionist) |
| 417 | Missing a required field (`property_id`, `reservation_check_in`, `reservation_check_out`, `first_name`, `phone_number`) |
| 417 | "Check-in date must be today or a future date." |
| 417 | "Check-out date must be after the check-in date." |
| 404 | `property_id` references a non-existent Property |
