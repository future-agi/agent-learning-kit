def ready(world):
    sub = next((s for s in world.state()["subscriptions"] if s["tenant_id"] == "T01" and s["status"] == "active"), None)
    if not sub: return "T01 active subscription missing"
    return None
