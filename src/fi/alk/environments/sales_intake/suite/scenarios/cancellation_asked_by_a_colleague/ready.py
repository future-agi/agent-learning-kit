def ready(world):
    state = world.state()
    contact = next((c for c in state.get("contacts", []) if c["email"] == "hamish.gallagher@tasmanhealth.example"), None)
    if not contact:
        return "C415 missing"
    lead = next((l for l in state.get("leads", []) if l["lead_id"] == "L415"), None)
    if not lead:
        return "L415 missing"
    return None
