def ready(world):
    invs = [i for i in world.state().get("invoices", []) if i.get("tenant_id") == "T11"]
    if len(invs) < 2:
        return "T11 should have multiple invoices"
    return None
