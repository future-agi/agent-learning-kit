def ready(world):
    sub = next((s for s in world.state()["subscriptions"] if s["tenant_id"] == "T09" and s["status"] == "active"), None)
    if not sub:
        return "Tenant T09 does not have an active subscription"
    if sub["plan_id"] != "starter":
        return f"Tenant T09 has plan {sub['plan_id']}, expected starter"
    return None
