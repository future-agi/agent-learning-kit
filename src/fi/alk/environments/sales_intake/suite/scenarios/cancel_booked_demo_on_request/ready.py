def ready(world):
    lead = next((l for l in world.state().get("leads", []) if l["lead_id"] == 'L10'), None)
    if not lead or lead["status"] != 'demo_booked':
        return "lead L10 is missing or not demo_booked"
    return None
