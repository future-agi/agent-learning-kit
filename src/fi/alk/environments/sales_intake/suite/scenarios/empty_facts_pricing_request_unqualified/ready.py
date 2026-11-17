def ready(world):
    lead = next((l for l in world.state().get("leads", []) if l["lead_id"] == "L21"), None)
    if not lead:
        return "lead L21 missing"
    return None