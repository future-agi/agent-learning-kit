def ready(world):
    state = world.state()
    disputes = state.get("disputes", [])
    d = next((x for x in disputes if x["dispute_id"] == "D106"), None)
    if not d:
        return "Dispute D106 must exist in world"
    return None
