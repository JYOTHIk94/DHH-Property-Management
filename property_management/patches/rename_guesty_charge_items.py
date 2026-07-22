import frappe

GROUP = "Services"


def execute():
	"""Recode Guesty charge Items from `GSTY-<family>-<label>` to the charge title.

	A Guesty charge is now billed against an Item whose code *and* name are the
	charge title, in the Services group. Items minted by the earlier scheme carry
	that title only as `item_name`, so without this they would keep being matched
	by name and every invoice would go on showing a `GSTY-` code that nothing in
	Guesty ever refers to.

	Renaming (rather than creating fresh Items) keeps the invoices already billed
	against them intact — Frappe repoints the links. Where two old Items share a
	title, the one nothing has billed is dropped and the billed one keeps the name.
	"""
	items = frappe.get_all(
		"Item",
		filters={"item_code": ["like", "GSTY-%"]},
		fields=["name", "item_name"],
	)

	# Billed items first: where two old codes share a title, the one carrying
	# invoice history must be the one that takes the name.
	items.sort(key=lambda i: -_billed(i.name))

	for item in items:
		title = (item.item_name or "").strip()[:140]
		if not title or title == item.name:
			continue

		if frappe.db.exists("Item", title):
			# The title is taken. Drop this one only if nothing was ever billed
			# against it; otherwise leave it alone rather than break an invoice.
			if not _billed(item.name):
				frappe.delete_doc("Item", item.name, force=True, ignore_permissions=True)
			continue

		frappe.rename_doc("Item", item.name, title, force=True, show_alert=False)
		if frappe.db.exists("Item Group", GROUP):
			frappe.db.set_value("Item", title, "item_group", GROUP)

	_drop_empty_guesty_group()


def _billed(item_code):
	return frappe.db.count("Sales Invoice Item", {"item_code": item_code})


def _drop_empty_guesty_group():
	"""The dedicated group the old scheme created is unused once the items move."""
	group = "Guesty Charges"
	if not frappe.db.exists("Item Group", group):
		return
	if frappe.db.count("Item", {"item_group": group}):
		return
	frappe.delete_doc("Item Group", group, force=True, ignore_permissions=True)
