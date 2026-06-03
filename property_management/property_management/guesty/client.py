# Copyright (c) 2026, Jyothi and contributors
# For license information, please see license.txt
"""Guesty Open API client.

Token handling is the critical concern here: Guesty allows only **5 access-token
requests per API key per 24h**, and each token is valid for 24h. So we cache the
token in ``Guesty Settings`` and only fetch a new one when the cached token is
within 60 minutes of expiry (or has been invalidated → 403). At ~1 fetch/day this
stays comfortably inside the 5/day budget.

See GUESTY_INTEGRATION_PLAN.md §2 and §4.2.
"""

import requests

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime

SETTINGS = "Guesty Settings"

# Refresh this many minutes before the token actually expires, so an in-flight
# request never races the expiry boundary.
REFRESH_MARGIN_MINUTES = 60


def get_settings():
	return frappe.get_single(SETTINGS)


def get_access_token(force=False):
	"""Return a valid bearer token, reusing the cached one when possible."""
	settings = get_settings()

	if not settings.enabled:
		frappe.throw(_("Guesty integration is disabled in Guesty Settings."))

	if not force and settings.access_token and settings.token_expiry:
		safe_until = add_to_date(now_datetime(), minutes=REFRESH_MARGIN_MINUTES)
		if get_datetime(settings.token_expiry) > safe_until:
			return settings.get_password("access_token")

	return _fetch_new_token(settings)


def _fetch_new_token(settings):
	client_id = settings.client_id
	client_secret = settings.get_password("client_secret") if settings.client_secret else None

	if not client_id or not client_secret:
		frappe.throw(_("Guesty Client ID and Client Secret are required in Guesty Settings."))

	try:
		resp = requests.post(
			settings.token_url,
			data={
				"grant_type": "client_credentials",
				"scope": "open-api",
				"client_id": client_id,
				"client_secret": client_secret,
			},
			headers={"Content-Type": "application/x-www-form-urlencoded"},
			timeout=30,
		)
		resp.raise_for_status()
	except requests.RequestException as e:
		frappe.log_error(frappe.get_traceback(), "Guesty token request failed")
		frappe.throw(_("Failed to obtain Guesty access token: {0}").format(str(e)))

	data = resp.json()
	token = data.get("access_token")
	if not token:
		frappe.throw(_("Guesty token response did not contain an access_token."))

	expires_in = int(data.get("expires_in") or 86400)

	settings.access_token = token
	settings.token_expiry = add_to_date(now_datetime(), seconds=expires_in)
	settings.save(ignore_permissions=True)
	frappe.db.commit()

	return token


def request(method, path, params=None, json=None, data=None):
	"""Make an authenticated call to the Guesty Open API.

	``path`` is relative to the configured base URL, e.g. ``"listings"`` or
	``"reservations"``. Retries exactly once on a 403 (expired/invalidated token)
	by forcing a token refresh.
	"""
	settings = get_settings()
	base = (settings.base_url or "").rstrip("/") + "/"
	url = base + path.lstrip("/")

	token = get_access_token()
	resp = _send(method, url, token, params, json, data)

	if resp.status_code == 403:
		token = get_access_token(force=True)
		resp = _send(method, url, token, params, json, data)

	resp.raise_for_status()
	return resp.json() if resp.text else None


def _send(method, url, token, params, json, data):
	return requests.request(
		method,
		url,
		headers={
			"Authorization": f"Bearer {token}",
			"Accept": "application/json",
		},
		params=params,
		json=json,
		data=data,
		timeout=60,
	)

