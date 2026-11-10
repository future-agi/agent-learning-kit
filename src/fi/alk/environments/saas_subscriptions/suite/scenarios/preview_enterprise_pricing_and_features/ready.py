def ready(world):
    sub = next((s for s in world.state()["subscriptions"] if s["tenant_id"] == "T07" and s["status"] == "active"), None)
    if not sub: return "Subscription T07 missing"
    if sub["plan_id"] != "basic": return "Plan not basic"
    return None