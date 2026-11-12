def ready(world):
    invs = [i for i in world.state().get("invoices", []) if i.get("tenant_id") == "T10"]
    if not invs:
        return "T10 invoices not found"
    return None
