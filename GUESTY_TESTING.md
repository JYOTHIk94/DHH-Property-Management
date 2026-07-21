
# Guesty Integration — Manual Testing Guide

Step-by-step manual test plan for the Guesty → ERPNext sync.
Pairs with `GUESTY_INTEGRATION_PLAN.md` (design) — this doc is what you actually *run*.

- **Site:** `dhh.com`
- **Bench:** `/home/jyothi/v16-bench`
- **App module path:** `property_management.property_management.guesty.*`
- **Direction:** read-only from Guesty (we only `GET`). Nothing is ever pushed back.

> Run every `bench` command from the bench root. All examples use `--site dhh.com`.

---

## 0. Conventions

| Symbol | Meaning |
|--------|---------|
| ✅ PASS | Observed result matches "Expected" |
| ❌ FAIL | Anything else — record actual output in the results table at the bottom |
| 🔁 | Test is safe to re-run (idempotent) |

Open a second terminal tailing the worker + error logs for the whole session:

```bash
cd /home/jyothi/v16-bench
bench --site dhh.com console        # keep handy for ad-hoc checks
tail -f logs/worker.error.log logs/web.error.log
```

Check the Frappe **Error Log** list (`/app/error-log`) after each failing step — the
integration logs there with titles starting `Guesty …`.

---

## 1. Pre-flight / Setup

### 1.1 Configure Guesty Settings
Go to **Guesty Settings** (single doctype) and fill in:

| Field | Value |
|-------|-------|
| `enabled` | ✅ checked |
| `client_id` | from Guesty Open API app |
| `client_secret` | from Guesty Open API app |
| `token_url` | `https://open-api.guesty.com/oauth2/token` |
| `base_url` | `https://open-api.guesty.com/v1` |
| `sync_listings` | ✅ checked |
| `sync_reservations` | ✅ checked |
| `default_property_type` | an existing Property Type (fallback) |
| `public_base_url` | a URL Guesty can reach (e.g. ngrok https URL) |

**Expected:** Saves without error. `access_token` / `token_expiry` / `webhook_id`
remain empty for now.

### 1.2 ⚠️ Token budget warning — READ BEFORE TESTING
Guesty allows only **5 access-token requests per API key per 24h**.
Every time you `force` a token (or clear `access_token`) you burn one.
The client caches the token and refreshes only within 60 min of expiry, so normal
testing costs ~1 token/day. **Do not loop token fetches.** If you exhaust the 5,
you are locked out for 24h.

---

## 2. Test: API Authentication (`client.py`)

### 2.1 First token fetch 🔁(careful — costs 1/5)
```bash
bench --site dhh.com console
```
```python
from property_management.property_management.guesty import client
tok = client.get_access_token()
print(tok[:12], "…")
```
**Expected:** prints a token prefix. In **Guesty Settings**, `access_token` is now
populated and `token_expiry` is ~24h in the future.

### 2.2 Token is cached (no second fetch)
Run `client.get_access_token()` again in the same console.
**Expected:** returns instantly, returns the **same** token, `token_expiry`
unchanged. (Confirms we are not burning the 5/day budget.)

### 2.3 Authenticated GET works
```python
data = client.request("GET", "listings", params={"limit": 1})
print(data.get("count"), len(data.get("results", [])))
```
**Expected:** a count and 0–1 results, no exception.

### 2.4 Disabled integration is refused
Uncheck `enabled`, save, then in console:
```python
client.get_access_token()
```
**Expected:** `frappe.exceptions.ValidationError: Guesty integration is disabled…`.
Re-check `enabled` before continuing.

---

## 3. Test: Listing → Property sync (`sync_listings.py`)

### 3.1 Full backfill 🔁
```bash
bench --site dhh.com execute property_management.property_management.guesty.sync_listings.run
```
**Expected:** returns `{'created': N, 'updated': M, 'failed': 0}`.
- First run: `created` > 0, `updated` = 0.
- `Property` list (`/app/property`) now shows records with `guesty_id` filled.
- In Guesty Settings, `last_listing_sync` is now (just now).

### 3.2 Idempotency — re-run 🔁
Run the same command again.
**Expected:** `created` = 0, `updated` = N (same N), **no duplicate Property rows**.
Verify:
```python
frappe.db.count("Property")   # should equal Guesty active+inactive listing count
```

### 3.3 Field mapping spot-check
Pick one Property, compare to its Guesty listing:

| Property field | Guesty source |
|----------------|---------------|
| `status` | `Active` if listing `active=true`, else `Inactive` |
| `property_type` | listing `propertyType` (auto-created if new) |
| `bedrooms` | `bedrooms` |
| `maximum_no_of_guests` | `accommodates` |
| `base_price_per_night` | `prices.basePrice` |
| `area` | `areaSquareFeet` |
| `guesty_id` | `_id` |

**Expected:** all match.

### 3.4 Auto-created Property Type
If a listing has a `propertyType` not in ERPNext.
**Expected:** a new **Property Type** record was created automatically; Property links to it.

### 3.5 Name collision handling
If two listings share the same title.
**Expected:** second Property name is suffixed `… (xxxxxx)` (last 6 of guesty_id);
no insert error.

---

## 4. Test: Reservation → Reservation sync (`sync_reservations.py`)

### 4.1 Backfill 🔁
```bash
bench --site dhh.com execute property_management.property_management.guesty.sync_reservations.run
```
**Expected:** `{'created': N, 'updated': M, 'skipped': S, 'failed': 0}`.
`last_reservation_sync` updated.

### 4.2 Reservations are DRAFT only (financial guard) — CRITICAL
Open any synced **Reservation**.
**Expected:**
- `docstatus` = **0 (Draft)** — never submitted.
- **No** Sales Order, Sales Invoice, or Payment Entry was created.
- `reservation_item` equals Guesty `money.fareAccommodation` (amount was *not*
  recomputed from the property — `from_guesty` flag suppresses that).

### 4.3 Past-date reservations are allowed
Find a reservation with a check-in date in the past.
**Expected:** it synced successfully (the `from_guesty` flag bypasses the
"check-in must be today/future" validation). A *manual* reservation with a past
check-in should still be rejected — confirm by creating one in the UI.

### 4.4 Status mapping
Check a few reservations across states:

| Guesty status | Reservation `reservation_status` |
|---------------|----------------------------------|
| inquiry / reserved / pending / awaiting_payment | Draft |
| confirmed | Confirmed |
| checkedin | Checked In |
| checkedout | Checked Out |
| canceled / cancelled / declined / expired | Cancelled |

### 4.5 Missing-phone guest
Find a Guesty guest with no/invalid phone.
**Expected:** Reservation still created; `phone_number` is **empty** (not a shared
placeholder), even though the field is normally mandatory — the sync sets
`ignore_mandatory`. Two phoneless guests must **not** collapse into one Customer.

### 4.6 Reservation before its listing (on-demand fetch)
Hard to force manually, but to verify the path: delete one Property that has a
reservation, then re-run the reservation sync.
**Expected:** the Property is re-fetched and re-created on demand; reservation links
correctly; not counted as `skipped`. (Re-run `sync_listings.run` afterward to be clean.)

### 4.7 Submitted record is protected
If you manually submit a synced Reservation (docstatus=1), then re-run the sync.
**Expected:** that record is counted as `skipped` and **left untouched** — the sync
never overwrites a submitted/cancelled ERPNext reservation.

---

## 5. Test: Webhook receiver (`webhook.py`)

The endpoint:
```
{public_base_url}/api/method/property_management.property_management.guesty.webhook.receive?secret=<webhook_secret>
```

First, in Guesty Settings generate/set a `webhook_secret` and save. Get its value:
```python
frappe.get_single("Guesty Settings").get_password("webhook_secret")
```

### 5.1 Reject: missing secret
```bash
curl -i -X POST "https://<public_base_url>/api/method/property_management.property_management.guesty.webhook.receive" \
  -H "Content-Type: application/json" \
  -d '{"event":"listing.updated","listing":{"_id":"TEST"}}'
```
**Expected:** HTTP **401**, body `{"ok": false, "error": "Unauthorized"}`.
An Error Log entry "Rejected Guesty webhook: bad/missing secret".

### 5.2 Reject: wrong secret
Same as above with `?secret=wrong`.
**Expected:** HTTP **401**.

### 5.3 Accept: valid listing event
```bash
curl -i -X POST "https://<public_base_url>/api/method/property_management.property_management.guesty.webhook.receive?secret=<REAL_SECRET>" \
  -H "Content-Type: application/json" \
  -d '{"event":"listing.updated","listing":{"_id":"<REAL_LISTING_ID>"}}'
```
**Expected:**
- HTTP **200**, body `{"ok": true, "event": "listing.updated", "id": "<REAL_LISTING_ID>"}`.
- Returns in well under 15s (heavy work is enqueued).
- `last_webhook_at` updated in Guesty Settings.
- Within ~5–30s a background job re-fetches the listing and the Property is
  created/updated. Confirm via the Property record's `modified` timestamp.

### 5.4 Accept: reservation event
Same with `{"event":"reservation.updated","reservation":{"_id":"<REAL_RES_ID>"}}`.
**Expected:** 200; the Reservation is upserted as Draft within ~30s.

### 5.5 Disabled integration
Uncheck `enabled`, save, then repeat 5.3.
**Expected:** HTTP **503**, `{"error":"Guesty integration disabled"}`. Re-enable after.

### 5.6 Listing removal → deactivate (not delete)
```bash
curl ... -d '{"event":"listing.removed","listing":{"_id":"<EXISTING_LISTING_ID>"}}'
```
**Expected:** 200; the matching Property flips to `status = Inactive`. The record
and its linked reservations are **preserved** (not deleted).

### 5.7 Unparseable payload doesn't trigger retries
Send valid secret but garbage body: `-d '{"foo":"bar"}'`.
**Expected:** HTTP **200** with `{"ok": true, "ignored": "unparseable"}` (so Guesty
won't retry forever) + an Error Log "Guesty webhook: unparseable payload".

### 5.8 Idempotent duplicate delivery 🔁
Fire the exact same valid request (5.3) twice.
**Expected:** no duplicate Property/Reservation — second delivery is an update on the
same `guesty_id`.

---

## 6. Test: Webhook registration with Guesty (`register` / `unregister`)

> Costs Guesty API calls (not tokens). Requires `webhook_secret` + `public_base_url` set.

### 6.1 Register
```bash
bench --site dhh.com execute property_management.property_management.guesty.webhook.register
```
**Expected:** `{'ok': True, 'webhook_id': '…', 'url': '…receive?secret=…'}`.
`webhook_id` saved in Guesty Settings. The URL/events visible in Guesty's dashboard.
Subscribed events: `listing.new/updated/removed`, `reservation.new/updated`.

### 6.2 Re-register is clean (no duplicates)
Run register again.
**Expected:** old webhook is unregistered first, a single fresh registration remains
(one webhook in Guesty, not two).

### 6.3 Unregister
```bash
bench --site dhh.com execute property_management.property_management.guesty.webhook.unregister
```
**Expected:** `{'ok': True, 'deleted': '…'}`; `webhook_id` cleared; webhook gone from
Guesty.

### 6.4 End-to-end live event
With the webhook registered, make a small edit to a listing **in Guesty** (e.g.
change the title).
**Expected:** within ~30s the corresponding Property updates in ERPNext without any
manual action. `last_webhook_at` advances.

---

## 7. Test: Daily reconciliation scheduler

The daily job (`hooks.py` → `scheduler_events.daily`) runs both syncs as a backstop.

### 7.1 Trigger manually
```bash
bench --site dhh.com execute frappe.utils.scheduler.enqueue_events --kwargs "{'site':'dhh.com'}"
# or just run the two run() functions directly as in §3.1 / §4.1
```
**Expected:** both syncs execute; `last_listing_sync` and `last_reservation_sync`
both advance; counts reflect any drift since the last webhook.

### 7.2 Backstop catches a missed webhook
Unregister the webhook (6.3), change a listing in Guesty, confirm Property does
**not** update, then run the daily sync.
**Expected:** the daily sync picks up the change — proving reconciliation works even
when webhooks are down.

---

## 8. Negative / resilience checks

| Scenario | How to induce | Expected |
|----------|---------------|----------|
| Bad credentials | Put a wrong `client_secret`, run §2.1 | `frappe.throw` "Failed to obtain Guesty access token…", Error Log entry, nothing partially written |
| Expired/invalid token mid-call | Clear `access_token`, run §2.3 | client transparently fetches a new token and the GET still succeeds |
| Guesty 403 on a data call | (observe in logs if it occurs) | client retries **once** with a forced token refresh |
| Missing `public_base_url` on register | clear it, run §6.1 | `frappe.throw` "Set a Public Base URL…" |
| Missing `webhook_secret` on register | clear it, run §6.1 | `frappe.throw` "Generate a Webhook Secret first." |
| One bad record in a batch | n/a (observe) | that record is logged to Error Log and counted in `failed`; the rest of the batch still syncs |

---

## 9. Cleanup after testing

```python
# In bench console — ONLY on a test site, never production data.
# Remove test artifacts if you injected fake guesty_ids (e.g. "TEST").
frappe.db.delete("Property", {"guesty_id": "TEST"})
frappe.db.commit()
```
Unregister the webhook if you don't want live events flowing into this site (§6.3).

---

## 10. Results log

| #   | Test                              | Result | Notes |
|-----|-----------------------------------|--------|-------|
| 2.1 | First token fetch                 |        |       |
| 2.2 | Token cached                      |        |       |
| 2.3 | Authenticated GET                 |        |       |
| 2.4 | Disabled refused                  |        |       |
| 3.1 | Listing backfill                  |        |       |
| 3.2 | Listing idempotency               |        |       |
| 3.3 | Field mapping                     |        |       |
| 4.1 | Reservation backfill              |        |       |
| 4.2 | Draft-only / no invoice           |        |       |
| 4.4 | Status mapping                    |        |       |
| 4.5 | Missing phone                     |        |       |
| 4.7 | Submitted record protected        |        |       |
| 5.1 | Reject missing secret             |        |       |
| 5.3 | Accept listing webhook            |        |       |
| 5.6 | Listing removal → Inactive        |        |       |
| 5.8 | Idempotent duplicate              |        |       |
| 6.1 | Register webhook                  |        |       |
| 6.4 | End-to-end live event             |        |       |
| 7.2 | Reconciliation backstop           |        |       |

> Mark each ✅/❌. For any ❌, paste the Error Log title + traceback next to it.
