def ready(world):
    sub = next((s for s in world.state()["subscriptions"] if s["tenant_id"] == "T12" and s["status"] == "active"), None)
    if not sub:
        return "Tenant T12 does not have an active subscription"
    if sub["plan_id"] != "pro":
        return f"Tenant T12 has plan {sub['plan_id']}, expected pro"
    return None
