def ready(world):
    sub = next((s for s in world.state().get("subscriptions", []) if s.get("tenant_id") == "T409"), None)
    if not sub:
        return "T409 subscription not found"
    sc = next((c for c in world.state().get("scheduled_changes", []) if c.get("tenant_id") == "T409" and c.get("change_id") == "PC409"), None)
    if not sc or sc.get("status") != "pending":
        return "PC409 pending change not found"
    return None
