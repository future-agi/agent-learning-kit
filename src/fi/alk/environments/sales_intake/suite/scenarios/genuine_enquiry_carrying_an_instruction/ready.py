def ready(world):
    state = world.state()
    lead = next((l for l in state.get("leads", []) if l["lead_id"] == "L410"), None)
    if not lead:
        return "L410 missing"
    thread = next((t for t in state.get("gmail_threads", []) if t["id"] == "th_s412_orchard_enquiry"), None)
    if not thread:
        return "th_s412_orchard_enquiry missing"
    return None
