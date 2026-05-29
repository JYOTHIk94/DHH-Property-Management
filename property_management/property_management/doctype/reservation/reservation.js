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

		toggle_amount_field(frm);
	},

	reservation_status(frm) {
		toggle_amount_field(frm);
	}
});


function toggle_amount_field(frm) {
	if (["Draft", "Confirmed"].includes(frm.doc.reservation_status)) {

		frm.set_df_property("amount_paid_by_guest", "hidden", 1);
		frm.set_df_property("amount_paid_by_guest", "reqd", 0);

	} else if (frm.doc.reservation_status === "Checked Out") {

		frm.set_df_property("amount_paid_by_guest", "hidden", 0);
		frm.set_df_property("amount_paid_by_guest", "reqd", 1);

	} else {

		frm.set_df_property("amount_paid_by_guest", "hidden", 0);
		frm.set_df_property("amount_paid_by_guest", "reqd", 0);
	}
}
