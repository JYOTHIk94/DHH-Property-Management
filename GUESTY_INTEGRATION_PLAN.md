# Guesty → ERPNext Real-Time Property & Reservation Sync

## Overview

This document describes how to integrate Guesty with ERPNext using Guesty Webhooks and the Guesty Open API.

The objective is:

* When a Property (Listing) is created or updated in Guesty, automatically create or update the corresponding Property record in ERPNext.
* When a Reservation is created or updated in Guesty, automatically create or update the corresponding Reservation record in ERPNext.
* Guesty remains the source of truth.
* ERPNext only receives and syncs data.
* No data is pushed back to Guesty.

---

# Architecture

```text
Guesty
   │
   │ Property Created / Updated
   │ Reservation Created / Updated
   ▼
Webhook Event
   ▼
ERPNext Webhook Endpoint
   ▼
Background Job Queue
   ▼
Guesty API
(GET Full Record)
   ▼
Create / Update ERPNext Record
```

---

# Integration Flow

## Step 1: Create Webhook Endpoint in ERPNext

Create a public API endpoint inside the custom app.

Example file:

```python
property_management/guesty/webhook.py
```

```python
import frappe

@frappe.whitelist(allow_guest=True)
def receive():
    payload = frappe.request.get_json()

    frappe.enqueue(
        "property_management.guesty.webhook.process_event",
        payload=payload,
        queue="default"
    )

    return {"success": True}
```

API URL:

```text
https://your-domain.com/api/method/property_management.guesty.webhook.receive
```

This URL will be registered inside Guesty.

---

# Step 2: Configure Webhook in Guesty

Inside Guesty:

1. Open Developer Settings.
2. Open Webhooks.
3. Create a new Webhook.
4. Enter ERPNext webhook URL.

Example:

```text
https://your-domain.com/api/method/property_management.guesty.webhook.receive
```

Subscribe to:

```text
listing.new
listing.updated
listing.removed

reservation.new
reservation.updated
```

Save configuration.

---

# Step 3: Process Webhook Event

The webhook payload should never be trusted as the complete source of data.

The payload is only used to identify:

```json
{
  "event": "listing.new",
  "resourceId": "123456"
}
```

Example processor:

```python
def process_event(payload):
    event = payload.get("event")
    resource_id = payload.get("resourceId")

    if event.startswith("listing"):
        process_listing(resource_id)

    elif event.startswith("reservation"):
        process_reservation(resource_id)
```

---

# Step 4: Authenticate with Guesty API

Guesty Open API uses OAuth2 Client Credentials.

Store the following in Guesty Settings:

```text
Client ID
Client Secret
Access Token
Token Expiry
```

Example token request:

```python
import requests

response = requests.post(
    TOKEN_URL,
    json={
        "clientId": client_id,
        "clientSecret": client_secret
    }
)

token = response.json()["access_token"]
```

Cache token locally.

Never request a token for every API call.

---

# Step 5: Fetch Full Listing Details

After receiving webhook:

```python
def process_listing(listing_id):
    listing = guesty_client.get_listing(listing_id)

    upsert_property(listing)
```

Example API:

```text
GET /listings/{listingId}
```

This guarantees ERPNext receives the latest complete data.

---

# Step 6: Upsert Property

Property records should be keyed using Guesty ID.

Custom Field:

```text
Property.guesty_id
```

Set as Unique.

Example:

```python
existing = frappe.db.exists(
    "Property",
    {"guesty_id": listing["id"]}
)
```

If property exists:

```python
doc = frappe.get_doc("Property", existing)

doc.property_name = listing["title"]
doc.status = "Active"

doc.save()
```

If property does not exist:

```python
doc = frappe.get_doc({
    "doctype": "Property",
    "property_name": listing["title"],
    "guesty_id": listing["id"],
    "status": "Active"
})

doc.insert()
```

---

# Step 7: Handle Listing Removal

Guesty listing removal should not delete ERPNext Property.

Instead:

```python
property.status = "Inactive"
```

This preserves history and linked reservations.

---

# Step 8: Fetch Reservation Details

For reservation events:

```python
def process_reservation(reservation_id):
    reservation = guesty_client.get_reservation(
        reservation_id
    )

    upsert_reservation(reservation)
```

API:

```text
GET /reservations/{reservationId}
```

---

# Step 9: Upsert Reservation

Create custom field:

```text
Reservation.guesty_id
```

Set as Unique.

Example:

```python
existing = frappe.db.exists(
    "Reservation",
    {"guesty_id": reservation["id"]}
)
```

Update if exists.

Insert if not exists.

Reservations should remain:

```text
Docstatus = 0
(Draft)
```

ERPNext should not automatically create:

* Sales Order
* Sales Invoice
* Payment Entry

Guesty remains the financial source of truth.

---

# Step 10: Background Jobs

Webhook requests must return quickly.

Recommended:

```python
frappe.enqueue(
    "property_management.guesty.webhook.process_event",
    payload=payload
)
```

Avoid:

```python
Webhook
    ↓
Call Guesty API
    ↓
Insert Property
    ↓
Return Response
```

Recommended:

```python
Webhook
    ↓
Queue Job
    ↓
Return 200
```

Guesty receives response immediately.

---

# Step 11: Daily Reconciliation Job

Even with webhooks, scheduled sync is required.

Reasons:

* Network outage
* Guesty retry exhaustion
* ERP downtime
* Webhook configuration issues

Create scheduler:

```python
scheduler_events = {
    "daily": [
        "property_management.guesty.sync_listings.run",
        "property_management.guesty.sync_reservations.run"
    ]
}
```

Daily sync ensures ERPNext eventually becomes consistent.

---

# Recommended Custom DocTypes

## Guesty Settings

Fields:

```text
enabled
client_id
client_secret
access_token
token_expiry

base_url
token_url

webhook_secret
public_base_url

last_property_sync
last_reservation_sync
```

Buttons:

```text
Test Connection
Sync Properties
Sync Reservations
Register Webhook
Unregister Webhook
Generate Secret
```

---

# Security Recommendations

## Do Not Hardcode Credentials

Never store:

```python
CLIENT_SECRET = "xxxxx"
```

inside source code.

Store credentials in:

```text
Guesty Settings
```

or

```text
site_config.json
```

---

## Validate Webhook Requests

If Guesty supports webhook secrets:

```text
X-Guesty-Secret
```

Validate before processing.

Reject unauthorized requests.

---

# Final Workflow

```text
Guesty Listing Created
        ↓
Webhook Sent
        ↓
ERPNext Endpoint
        ↓
Background Job
        ↓
Fetch Listing Using Guesty API
        ↓
Upsert Property
        ↓
Property Available In ERPNext
```

Expected synchronization delay:

```text
5–30 seconds
```

depending on queue processing speed.
