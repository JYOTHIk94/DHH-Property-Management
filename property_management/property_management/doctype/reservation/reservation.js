frappe.ui.form.on("Reservation", {
	refresh(frm) {
		// Property filter
		frm.set_query("property_id", function() {
			return {
				filters: {
					status: "Active"
				}
			};
		});
	},

	reservation_status(frm) {
		// Lifecycle status — persist a cancellation so on_update can reverse docs.
		if (frm.doc.reservation_status === "Cancelled" && frm.is_dirty()) {
			frm.save();
		}
	},

	guest_status(frm) {
		// Stay status drives billing — persist check-in / checkout so the server
		// (on_update) raises the invoice + payment.
		if (["Checkin", "Checkout"].includes(frm.doc.guest_status) && frm.is_dirty()) {
			frm.save();
		}
	}
});
