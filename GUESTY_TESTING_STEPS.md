# Guesty Integration — Step-by-Step Testing Runbook

A linear "do this, then this" walkthrough. Run top to bottom, tick each box, and
record the result. For deeper per-case detail see `GUESTY_TESTING.md`.

- **Site:** `dhh.com`  |  **Bench:** `/home/jyothi/v16-bench`
- **Direction:** read-only from Guesty (we only `GET`; nothing is pushed back)
- ⚠️ **Token budget:** Guesty allows only **5 access-token requests per API key / 24h**.
  The client caches the token, so normal testing costs ~1/day. **Never loop token fetches.**

---

## Phase 0 — Open your terminals (do this once)

**Terminal A — web + workers (leave running the whole session):**
```
cd /home/jyothi/v16-bench && bench start
```

**Terminal B — live logs:**
```
cd /home/jyothi/v16-bench && tail -f logs/worker.error.log logs/web.error.log
```

**Terminal C — your working terminal** for the commands below.

Also keep the browser open at `https://dhh.com/app` and check the **Error Log**
list (`/app/error-log`) after anything fails — our entries start with `Guesty …`.

- [ ] Three terminals open, `bench start` is up (you can load `https://dhh.com/app`)

---

## Phase 1 — Confirm configuration

**Step 1.1 — Check settings (no secrets printed):**
```bash
cd /home/jyothi/v16-bench && bench --site dhh.com execute frappe.client.get_value \
  --args "['Guesty Settings', ['enabled','base_url','token_url','sync_listings','sync_reservations','default_property_type','public_base_url','webhook_id','last_webhook_at']]"
```
**Expect:** `enabled=1`, both sync flags `1`, `base_url` and `token_url` set,
`webhook_id` present, `public_base_url` is your current tunnel URL.

- [ ] 1.1 Settings look correct
- [ ] ⚠️ If `default_property_type` is empty, set it to a real Property Type (e.g. "Apartment") in **Guesty Settings** — it's the fallback for listings with no type.

---

## Phase 2 — Authentication (costs at most 1 token)

**Step 2.1 — Token reuse + live API call:**
```bash
cd /home/jyothi/v16-bench
bench --site dhh.com execute property_management.property_management.guesty.client.request \
  --args "['GET','listings',{'limit':1}]"
```
**Expect:** a JSON dict with a `count` and a one-item `results` list — proves
credentials work and a token is available (reused if still valid).

- [ ] 2.1 Returns listings JSON, no error

**Step 2.2 — Disabled is refused (optional):** uncheck `enabled` in Guesty Settings,
re-run 2.1 → expect a "Guesty integration is disabled" error. **Re-check `enabled` after.**

- [ ] 2.2 Disabled correctly refused (then re-enabled)

---

## Phase 3 — Listing → Property sync

**Step 3.1 — Run the listing sync:**
```bash
cd /home/jyothi/v16-bench && bench --site dhh.com execute \
  property_management.property_management.guesty.sync_listings.run
```
**Expect:** `{'created': X, 'updated': Y, 'failed': 0}`.

- [ ] 3.1 `failed: 0`

**Step 3.2 — Idempotency: run it a second time.**
**Expect:** `created: 0`, same total, **no new Property rows**:
```bash
bench --site dhh.com execute frappe.client.get_count --args "['Property']"
```

- [ ] 3.2 `created: 0`, Property count unchanged

**Step 3.3 — Spot-check one mapping** in the UI (`/app/property`): open a Property
with a `guesty_id` and compare to its Guesty listing — `status` (Active/Inactive),
`property_type`, `bedrooms`, `maximum_no_of_guests`, `base_price_per_night`.

- [ ] 3.3 Fields match Guesty

---

## Phase 4 — Reservation → Reservation sync

**Step 4.1 — Run the reservation sync:**
```bash
cd /home/jyothi/v16-bench && bench --site dhh.com execute \
  property_management.property_management.guesty.sync_reservations.run
```
**Expect:** `{'created': X, 'updated': Y, 'skipped': S, 'failed': 0}`.

- [ ] 4.1 `failed: 0`

**Step 4.2 — Financial guard (CRITICAL):** open any synced Reservation (`/app/reservation`).
**Expect:** `docstatus = 0` (Draft); **no** Sales Order / Sales Invoice / Payment Entry;
`reservation_item` equals Guesty's `money.fareAccommodation` (not recomputed).

- [ ] 4.2 All synced reservations are Draft; no invoices created

**Step 4.3 — Verify no draft was accidentally submitted:**
```bash
bench --site dhh.com execute frappe.client.get_count \
  --args "['Reservation', {'guesty_id': ['!=',''], 'docstatus': 1}]"
```
**Expect:** `0`.

- [ ] 4.3 Zero submitted Guesty-synced reservations

**Step 4.4 — Status + phone spot-checks** in the UI: a cancelled Guesty booking →
`reservation_status = Cancelled`; a guest with no phone → `phone_number` empty
(record still created, not blocked).

- [ ] 4.4 Status mapping + empty-phone handling correct

---

## Phase 5 — Webhook endpoint (needs `bench start` from Phase 0)

**Step 5.1 — Endpoint is alive (expect HTTP 401 — auth rejects the bad secret):**
```bash
curl -i -X POST "$(bench --site dhh.com execute property_management.property_management.guesty.webhook._callback_url --args '[]' 2>/dev/null | tr -d '"' | sed 's/?secret=.*//')" \
  -H "Content-Type: application/json" \
  -d '{"event":"listing.updated","listing":{"_id":"TEST"}}'
```
*(Or just hit your tunnel URL `…/api/method/property_management.property_management.guesty.webhook.receive` with no secret.)*
**Expect:** `HTTP/1.1 401` and `{"ok": false, "error": "Unauthorized"}`.

- [ ] 5.1 Bad/missing secret → 401

**Step 5.2 — Generate the real authenticated curl** (prints the exact command with
the live secret + a real listing id; nothing to type by hand):
```bash
cd /home/jyothi/v16-bench
bench --site dhh.com console <<'PY'
import frappe
s = frappe.get_single("Guesty Settings")
base = s.public_base_url.rstrip("/")
secret = s.get_password("webhook_secret")
lid = frappe.db.get_value("Property", {"guesty_id": ["!=", ""]}, "guesty_id")
url = f"{base}/api/method/property_management.property_management.guesty.webhook.receive?secret={secret}"
print("\n--- paste this into Terminal C ---")
print(f'''curl -i -X POST "{url}" -H "Content-Type: application/json" -d '{{"event":"listing.updated","listing":{{"_id":"{lid}"}}}}' ''')
PY
```
Paste the printed `curl` command and run it.
**Expect:** `HTTP/1.1 200` and `{"ok": true, "event": "listing.updated", "id": "..."}`.
Within ~30s the matching Property's `modified` timestamp updates and
`last_webhook_at` advances.

- [ ] 5.2 Valid event → 200, Property re-synced in background

**Step 5.3 — Removal deactivates (doesn't delete):** repeat 5.2 but with
`"event":"listing.removed"`. **Expect:** 200; that Property flips to
`status = Inactive`; the record and its reservations are preserved.

- [ ] 5.3 `listing.removed` → Inactive, not deleted

---

## Phase 6 — Webhook registration with Guesty

Your tunnel URL changes each `cloudflared`/`bench` restart. If `public_base_url`
changed since the last registration, **re-register** so Guesty points at the live URL.

**Step 6.1 — Re-register:**
```bash
cd /home/jyothi/v16-bench && bench --site dhh.com execute \
  property_management.property_management.guesty.webhook.register
```
**Expect:** `{'ok': True, 'webhook_id': '...', 'url': '...receive?secret=...'}`.
(Re-running is safe — it unregisters the old one first; no duplicates.)

- [ ] 6.1 Registered, single webhook in Guesty dashboard

**Step 6.2 — TRUE end-to-end:** edit a listing's title **in the Guesty dashboard**.
**Expect:** within ~30s the Property updates in ERPNext with no manual action;
`last_webhook_at` advances. Watch Terminal B for the background job.

- [ ] 6.2 Live Guesty edit flows through to ERPNext

---

## Phase 7 — Daily reconciliation backstop

**Step 7.1 — Run both syncs as the scheduler would:** repeat Steps 3.1 + 4.1.
**Expect:** `last_listing_sync` and `last_reservation_sync` advance; counts reflect
any drift. This is what runs daily even if every webhook were dropped.

- [ ] 7.1 Daily reconciliation runs clean

---

## Done — summary

| Phase | What it proves | Result |
|-------|----------------|--------|
| 1 | Configuration present | |
| 2 | Auth + live API | |
| 3 | Listings → Property, idempotent | |
| 4 | Reservations → Reservation, Draft-only (no invoices) | |
| 5 | Webhook endpoint: auth, upsert, removal | |
| 6 | Live Guesty → ERPNext end-to-end | |
| 7 | Daily reconciliation backstop | |

For any failure: open `/app/error-log`, find the `Guesty …` entry, and paste the
title + traceback next to the failing step.
