def ready(world):
    lead = next((l for l in world.state().get("leads", []) if l["lead_id"] == 'L07'), None)
    if not lead or lead["status"] != 'needs_information':
        return "lead L07 is missing or not needs_information"
    return None
