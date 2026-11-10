def ready(world):
    invs = {i["invoice_id"] for i in world.state().get("invoices", [])}
    if "inv_403_curr" not in invs:
        return "missing inv_403_curr"
    return None
