def ready(world):
    sub = next((s for s in world.state()["subscriptions"] if s["tenant_id"] == "T10" and s["status"] == "active"), None)
    if not sub: return "Tenant T10 active subscription missing"
    return None