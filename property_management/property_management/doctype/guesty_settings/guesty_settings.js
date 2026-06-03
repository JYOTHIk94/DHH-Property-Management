// Copyright (c) 2026, Jyothi and contributors
// For license information, please see license.txt

frappe.ui.form.on("Guesty Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test Connection"), () => {
			frm.call({
				doc: frm.doc,
				method: "test_connection",
				freeze: true,
				freeze_message: __("Connecting to Guesty…"),
			}).then((r) => {
				if (r.message && r.message.ok) {
					const sample = r.message.sample
						? __("Sample listing: {0}", [r.message.sample])
						: __("No listings returned.");
					frappe.msgprint({
						title: __("Connection successful"),
						message: `${__("Total listings: {0}", [r.message.count ?? "?"])}<br>${sample}`,
						indicator: "green",
					});
				}
			});
		});

		frm.add_custom_button(
			__("Sync Listings Now"),
			() => run_sync(frm, "sync_listings_now", __("Syncing listings…")),
			__("Sync")
		);

		frm.add_custom_button(
			__("Sync Reservations Now"),
			() => run_sync(frm, "sync_reservations_now", __("Syncing reservations…")),
			__("Sync")
		);

		frm.add_custom_button(
			__("Generate Secret"),
			() => {
				frm.call({ doc: frm.doc, method: "generate_secret", freeze: true }).then(() => {
					frm.reload_doc();
					frappe.show_alert({ message: __("Webhook secret generated."), indicator: "green" });
				});
			},
			__("Webhooks")
		);

		frm.add_custom_button(
			__("Register Webhook"),
			() => {
				frm.call({
					doc: frm.doc,
					method: "register_webhook",
					freeze: true,
					freeze_message: __("Registering webhook with Guesty…"),
				}).then((r) => {
					const res = r.message || {};
					frm.reload_doc();
					frappe.msgprint({
						title: __("Webhook registered"),
						message: __("Webhook ID: {0}<br>URL: {1}", [
							res.webhook_id || "?",
							frappe.utils.escape_html(res.url || ""),
						]),
						indicator: "green",
					});
				});
			},
			__("Webhooks")
		);

		frm.add_custom_button(
			__("Unregister Webhook"),
			() => {
				frm.call({ doc: frm.doc, method: "unregister_webhook", freeze: true }).then(() => {
					frm.reload_doc();
					frappe.show_alert({ message: __("Webhook unregistered."), indicator: "orange" });
				});
			},
			__("Webhooks")
		);
	},
});

function run_sync(frm, method, message) {
	frm.call({ doc: frm.doc, method, freeze: true, freeze_message: message }).then((r) => {
		const res = r.message || {};
		if (res.skipped) {
			frappe.msgprint({
				title: __("Skipped"),
				message: __("Sync is disabled in settings ({0}).", [res.skipped]),
				indicator: "orange",
			});
			return;
		}
		frappe.msgprint({
			title: __("Sync complete"),
			message: __("Created: {0} · Updated: {1} · Skipped: {2} · Failed: {3}", [
				res.created ?? 0,
				res.updated ?? 0,
				res.skipped ?? 0,
				res.failed ?? 0,
			]),
			indicator: res.failed ? "orange" : "green",
		});
	});
}
