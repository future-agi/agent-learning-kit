def ready(world):
    inv = next((i for i in world.state()["invoices"] if i["invoice_id"] == "inv_07_curr" and i["tenant_id"] == "T07"), None)
    if not inv: return "Invoice inv_07_curr missing"
    return None