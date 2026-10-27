def ready(world):
    invs = [i for i in world.state().get("invoices", []) if i.get("tenant_id") == "T01"]
    if len(invs) < 2:
        return "Need at least 2 invoices for T01"
    return None
