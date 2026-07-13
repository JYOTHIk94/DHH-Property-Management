from frappe import _


def get_data():
	# Sales Invoice + Payment Entry carry a `reservation` back-link (Custom Field),
	# so all stage invoices/payments show under the Reservation's Connections.
	return {
		"fieldname": "reservation",
		"transactions": [
			{
				"label": _("Reference"),
				"items": ["Sales Invoice", "Payment Entry"],
			},
		],
	}
