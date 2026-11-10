def ready(world):
    sc = next((c for c in world.state().get("scheduled_changes", []) if c.get("tenant_id") == "T406" and c.get("change_id") == "PC406"), None)
    if not sc or sc.get("status") != "canceled":
        return "PC406 canceled change record not found"
    return None
