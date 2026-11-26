def ready(world):
    sub = next((s for s in world.state().get("subscriptions", []) if s.get("tenant_id") == "T05" and s.get("status") == "active"), None)
    if not sub:
        return "T05 active subscription not found"
    return None
