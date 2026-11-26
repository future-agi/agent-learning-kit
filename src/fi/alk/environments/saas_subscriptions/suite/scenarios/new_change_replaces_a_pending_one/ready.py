def ready(world):
    sub = next((s for s in world.state().get("subscriptions", []) if s.get("tenant_id") == "T405"), None)
    if not sub:
        return "T405 subscription not found"
    sc = next((c for c in world.state().get("scheduled_changes", []) if c.get("tenant_id") == "T405" and c.get("change_id") == "PC405"), None)
    if not sc or sc.get("status") != "pending":
        return "PC405 pending change not found"
    return None
