def ready(world):
    sub = next((s for s in world.state().get("subscriptions", []) if s.get("tenant_id") == "T03" and s.get("status") == "active"), None)
    if not sub:
        return "T03 active subscription not found"
    return None
