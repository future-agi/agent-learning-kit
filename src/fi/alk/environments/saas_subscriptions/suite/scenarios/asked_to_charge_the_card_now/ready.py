def ready(world):
    pay = next((p for p in world.state().get("payments", []) if p.get("tenant_id") == "T11" and p.get("invoice_id") == "inv_11_addon"), None)
    if not pay or pay.get("status") != "retrying":
        return "inv_11_addon retrying payment record not found"
    return None
