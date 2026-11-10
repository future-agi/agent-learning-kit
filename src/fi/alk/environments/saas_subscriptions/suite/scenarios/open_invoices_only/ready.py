def ready(world):
    invs = [i for i in world.state().get("invoices", []) if i.get("tenant_id") == "T09"]
    if not invs:
        return "missing invoices for T09"
    return None
