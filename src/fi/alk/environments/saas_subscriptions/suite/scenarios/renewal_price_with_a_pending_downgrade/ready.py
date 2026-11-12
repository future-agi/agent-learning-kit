def ready(world):
    terms = next((t for t in world.state().get("renewal_terms", []) if t.get("tenant_id") == "T407"), None)
    if not terms:
        return "T407 renewal terms not found"
    sc = next((c for c in world.state().get("scheduled_changes", []) if c.get("tenant_id") == "T407" and c.get("change_id") == "PC407"), None)
    if not sc or sc.get("status") != "pending":
        return "PC407 pending change not found"
    return None
