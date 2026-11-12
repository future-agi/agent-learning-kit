def ready(world):
    lead = next((l for l in world.state().get("leads", []) if l["lead_id"] == 'L11'), None)
    if not lead or lead["status"] != 'needs_information':
        return "lead L11 is missing or not needs_information"
    return None
