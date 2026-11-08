def ready(world):
    d = next((x for x in world.state().get("disputes", []) if x["dispute_id"] == "D03"), None)
    if not d or d["status"] != "resolved":
        return "D03 is missing or not resolved"
    return None
