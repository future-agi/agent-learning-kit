def ready(world):
    sub = next((s for s in world.state()["subscriptions"] if s["tenant_id"] == "T11" and s["status"] == "active"), None)
    if not sub:
        return "Tenant T11 does not have an active subscription"
    if sub["plan_id"] != "basic":
        return f"Tenant T11 has plan {sub['plan_id']}, expected basic"
    return None
