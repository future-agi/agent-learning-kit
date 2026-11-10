def ready(world):
    state = world.state()
    lead = next((l for l in state.get("leads", []) if l["lead_id"] == "L418"), None)
    if not lead or lead["status"] != "demo_booked":
        return "L418 demo_booked missing"
    return None
