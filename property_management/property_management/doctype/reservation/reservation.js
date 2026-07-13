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
		// Reservation is non-submittable — just persist the status change. The
		// server (on_update) handles check-in and checkout based on the status.
		if (["Checked In", "Checked Out", "Cancelled"].includes(frm.doc.reservation_status) && frm.is_dirty()) {
			frm.save();
		}
	}
});
