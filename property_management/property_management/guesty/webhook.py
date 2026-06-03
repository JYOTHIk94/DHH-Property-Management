# Copyright (c) 2026, Jyothi and contributors
# For license information, please see license.txt
"""Real-time Guesty → ERPNext sync via webhooks.

Flow:
    1. Guesty fires an event (e.g. ``listing.new``, ``reservation.updated``) and
       POSTs a small payload to our public endpoint ``receive``.
    2. ``receive`` authenticates the call (shared ``secret`` embedded in the URL —
       Guesty Open API v1 webhooks are *not* HMAC-signed), enqueues a background
       job, and returns ``2xx`` immediately (Guesty requires a response within
       15s, so we never do the heavy work inline).
    3. The background job ``_process`` re-fetches the **full** record from the
       normal Guesty API (the webhook payload may be partial) and upserts it into
       Property / Reservation by reusing the existing sync ``upsert_one`` logic.

Polling (see hooks.py scheduler_events) is kept as a reconciliation backstop in
case a webhook is ever dropped. See GUESTY_INTEGRATION_PLAN.md §3, §4.5.
"""

import hmac
import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from property_management.property_management.guesty import client

SETTINGS = "Guesty Settings"

# Events we subscribe to with Guesty. listing.* drives Property; reservation.*
# drives Reservation.
SUBSCRIBED_EVENTS = [
	"listing.new",
	"listing.updated",
	"listing.removed",
	"reservation.new",
	"reservation.updated",
]


# ---------------------------------------------------------------------------
# Receiver — the public endpoint Guesty calls
# ---------------------------------------------------------------------------
@frappe.whitelist(allow_guest=True)
def receive(secret=None):
	"""Public endpoint Guesty POSTs events to.

	URL form (registered with Guesty):
	    {public_base_url}/api/method/property_management.property_management.guesty.webhook.receive?secret=...

	Authenticates via the shared secret, enqueues processing, and returns fast.
	"""
	settings = frappe.get_single(SETTINGS)

	if not settings.enabled:
		return _reject(503, "Guesty integration disabled")

	if not _secret_ok(settings, secret):
		# Don't leak which part failed; log for the operator.
		frappe.log_error("Rejected Guesty webhook: bad/missing secret", "Guesty webhook")
		return _reject(401, "Unauthorized")

	payload = _read_payload()
	event = _extract_event(payload)
	object_id = _extract_object_id(payload)

	# Stamp receipt for observability (cheap; safe within the 15s budget).
	settings.db_set("last_webhook_at", now_datetime(), update_modified=False)
	frappe.db.commit()

	if not event or not object_id:
		frappe.log_error(
			f"event={event!r} object_id={object_id!r}\npayload={json.dumps(payload)[:1000]}",
			"Guesty webhook: unparseable payload",
		)
		# Still 200 so Guesty doesn't retry a payload we can't parse anyway.
		return {"ok": True, "ignored": "unparseable"}

	# Heavy work (API re-fetch + upsert) runs in the background so we answer in
	# well under 15s. Idempotent on guesty_id, so duplicate deliveries are safe.
	frappe.enqueue(
		"property_management.property_management.guesty.webhook._process",
		queue="short",
		event=event,
		object_id=object_id,
	)

	return {"ok": True, "event": event, "id": object_id}


def _process(event, object_id):
	"""Background job: re-fetch the full record from Guesty and upsert it."""
	try:
		if event.startswith("listing"):
			_process_listing(event, object_id)

		elif event.startswith("reservation"):
			_process_reservation(event, object_id)
		else:
			frappe.logger("guesty").info(f"Webhook event ignored: {event}")
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Guesty webhook processing failed: {event} {object_id}",
		)


def _process_listing(event, listing_id):
	from property_management.property_management.guesty import sync_listings

	if event == "listing.removed":
		# Don't hard-delete; deactivate so historical reservations keep their link.
		name = frappe.db.get_value("Property", {"guesty_id": listing_id}, "name")
		if name:
			frappe.db.set_value("Property", name, "status", "Inactive")
			frappe.db.commit()
		return

	listing = _fetch(f"listings/{listing_id}")
	if listing and listing.get("_id"):
		sync_listings.upsert_one(listing)
		frappe.db.commit()


def _process_reservation(event, reservation_id):
	from property_management.property_management.guesty import sync_reservations

	reservation = _fetch(f"reservations/{reservation_id}")
	if reservation and reservation.get("_id"):
		# Make sure the listing exists first (upsert_one resolves it, but a fresh
		# Guesty listing may not be synced yet).
		sync_reservations.upsert_one(reservation)
		frappe.db.commit()


def _fetch(path):
	"""GET a single object from Guesty; unwrap the occasional ``results`` form."""
	data = client.request("GET", path)
	if isinstance(data, dict) and data.get("results") and not data.get("_id"):
		data = data["results"]
	if isinstance(data, list):
		data = data[0] if data else None
	return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# Registration — manage the webhook on the Guesty side
# ---------------------------------------------------------------------------
@frappe.whitelist()
def register():
	"""Register (or re-register) this site's webhook endpoint with Guesty."""
	settings = frappe.get_single(SETTINGS)

	if not settings.webhook_secret:
		frappe.throw(_("Generate a Webhook Secret first."))
	if not settings.public_base_url:
		frappe.throw(_("Set a Public Base URL that Guesty can reach (e.g. your ngrok URL)."))

	# Replace any existing registration so we don't pile up duplicates.
	if settings.webhook_id:
		try:
			unregister()
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Guesty webhook: unregister-before-register failed")

	url = _callback_url(settings)
	resp = client.request(
		"POST",
		"webhooks",
		json={"url": url, "events": SUBSCRIBED_EVENTS},
	) or {}

	webhook_id = resp.get("_id") or resp.get("id")
	if not webhook_id:
		frappe.throw(_("Guesty did not return a webhook id. Response: {0}").format(json.dumps(resp)[:500]))

	settings.db_set("webhook_id", webhook_id)
	frappe.db.commit()
	return {"ok": True, "webhook_id": webhook_id, "url": url}


@frappe.whitelist()
def unregister():
	"""Delete the registered webhook from Guesty."""
	settings = frappe.get_single(SETTINGS)
	webhook_id = settings.webhook_id
	if not webhook_id:
		return {"ok": True, "note": "nothing registered"}

	client.request("DELETE", f"webhooks/{webhook_id}")
	settings.db_set("webhook_id", "")
	frappe.db.commit()
	return {"ok": True, "deleted": webhook_id}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _callback_url(settings):
	base = (settings.public_base_url or "").rstrip("/")
	secret = settings.get_password("webhook_secret")
	return (
		f"{base}/api/method/property_management.property_management.guesty.webhook.receive"
		f"?secret={secret}"
	)


def _secret_ok(settings, provided):
	expected = settings.get_password("webhook_secret") if settings.webhook_secret else None
	if not expected or not provided:
		return False
	return hmac.compare_digest(str(provided), str(expected))


def _read_payload():
	"""Parse the JSON body Guesty sent; fall back to form_dict."""
	try:
		raw = frappe.request.get_data(as_text=True) if frappe.request else None
		if raw:
			return json.loads(raw)
	except Exception:
		pass
	data = dict(frappe.form_dict or {})
	data.pop("secret", None)
	return data


def _extract_event(payload):
	if not isinstance(payload, dict):
		return None
	return payload.get("event") or payload.get("type")


def _extract_object_id(payload):
	"""Pull the Guesty object _id out of whatever shape the payload arrived in."""
	if not isinstance(payload, dict):
		return None

	for key in ("listing", "reservation", "object", "payload", "data"):
		obj = payload.get(key)
		if isinstance(obj, dict):
			oid = obj.get("_id") or obj.get("id")
			if oid:
				return oid

	return payload.get("_id") or payload.get("id") or payload.get("objectId")


def _reject(status, message):
	frappe.local.response["http_status_code"] = status
	return {"ok": False, "error": message}

