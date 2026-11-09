def ready(world):
    sc = next((c for c in world.state().get("scheduled_changes", []) if c.get("change_id") == "PC04"), None)
    if not sc or sc.get("status") != "pending":
        return "PC04 pending scheduled change must exist"
    return None
