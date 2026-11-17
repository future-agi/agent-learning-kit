def ready(world):
    sub = next((s for s in world.state().get("subscriptions", []) if s.get("tenant_id") == "T01" and s.get("status") == "active"), None)
    if not sub:
        return "T01 active subscription not found"
    if sub.get("plan_id") != "pro":
        return f"T01 plan should be pro, got {sub.get('plan_id')}"
    return None
