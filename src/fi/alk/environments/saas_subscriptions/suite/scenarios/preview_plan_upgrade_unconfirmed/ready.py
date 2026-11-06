def ready(world):
    sub = next((s for s in world.state().get("subscriptions", []) if s.get("tenant_id") == "T09" and s.get("status") == "active"), None)
    if not sub or sub.get("plan_id") != "starter":
        return "T09 must have an active starter subscription"
    return None
