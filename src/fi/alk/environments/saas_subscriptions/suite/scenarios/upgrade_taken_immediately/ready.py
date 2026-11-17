def ready(world):
    sub = next((s for s in world.state().get("subscriptions", []) if s.get("tenant_id") == "T07" and s.get("status") == "active"), None)
    if not sub:
        return "T07 active subscription not found"
    if sub.get("plan_id") != "basic":
        return f"T07 plan should be basic, got {sub.get('plan_id')}"
    return None
