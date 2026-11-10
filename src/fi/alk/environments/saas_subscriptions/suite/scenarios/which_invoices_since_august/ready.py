def ready(world):
    invs = [i for i in world.state().get("invoices", []) if i.get("tenant_id") == "T12"]
    if len(invs) < 2:
        return "expected at least 2 invoices for T12"
    return None
