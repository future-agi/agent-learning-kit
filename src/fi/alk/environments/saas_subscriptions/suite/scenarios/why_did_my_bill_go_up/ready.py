def ready(world):
    invs = {i["invoice_id"] for i in world.state().get("invoices", [])}
    if "inv_02_curr" not in invs:
        return "missing inv_02_curr"
    if "inv_02_prev" not in invs:
        return "missing inv_02_prev"
    return None
