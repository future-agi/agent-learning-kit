def ready(world):
    sub = next((s for s in world.state().get("subscriptions", []) if s.get("tenant_id") == "T408"), None)
    if not sub:
        return "T408 subscription not found"
    sc = next((c for c in world.state().get("scheduled_changes", []) if c.get("tenant_id") == "T408" and c.get("change_id") == "PC408"), None)
    if not sc or sc.get("status") != "pending":
        return "PC408 pending change not found"
    return None
