def ready(world):
    sub = next((s for s in world.state()["subscriptions"] if s["tenant_id"] == "T02" and s["status"] == "active"), None)
    if not sub: return "Subscription T02 missing"
    return None