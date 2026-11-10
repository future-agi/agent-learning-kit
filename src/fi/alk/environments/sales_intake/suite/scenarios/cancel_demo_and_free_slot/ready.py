def ready(world):
    lead = next((l for l in world.state().get("leads", []) if l["lead_id"] == "L401"), None)
    if not lead or lead["status"] != "demo_booked":
        return "lead L401 is missing or not demo_booked"
    return None
