def ready(world):
    inv = next((i for i in world.state().get("invoices", []) if i.get("invoice_id") == "inv_08_curr"), None)
    if not inv or inv.get("tenant_id") == "T07":
        return "Invoice inv_08_curr must exist and not belong to T07"
    return None
