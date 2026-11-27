def ready(world):
    state = world.state()
    contact = next((c for c in state.get("contacts", []) if c["contact_id"] == "C421"), None)
    if not contact or contact["consent_status"] != "opted_in":
        return "C421 opted_in missing"
    demo = next((d for d in state.get("demos", []) if d["demo_id"] == "D421"), None)
    if not demo or demo["status"] != "booked":
        return "D421 booked missing"
    return None
