def ready(world):
    lead = next((l for l in world.state().get("leads", []) if l["lead_id"] == 'L02'), None)
    if not lead or lead["status"] != 'new':
        return "lead L02 is missing or not new"
    return None
