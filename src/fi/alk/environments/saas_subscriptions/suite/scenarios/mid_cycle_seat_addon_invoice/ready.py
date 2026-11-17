def ready(world):
    invs = {i["invoice_id"] for i in world.state().get("invoices", [])}
    if "inv_11_addon" not in invs:
        return "missing inv_11_addon"
    return None
