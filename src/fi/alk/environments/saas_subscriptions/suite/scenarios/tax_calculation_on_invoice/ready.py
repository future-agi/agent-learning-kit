def ready(world):
    invs = {i["invoice_id"] for i in world.state().get("invoices", [])}
    if "inv_05_curr" not in invs:
        return "missing inv_05_curr"
    return None
