def ready(world):
    holds = world.state().get("slot_holds", [])
    h401 = next((h for h in holds if h["hold_id"] == "H401"), None)
    if not h401 or h401["status"] != "active":
        return "hold H401 is missing or not active"
    return None