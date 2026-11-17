def ready(world):
    state = world.state()
    disp = state.get("disputes", [])
    if not any(d["dispute_id"] == "D01" for d in disp):
        return "Dispute D01 missing"
    return None