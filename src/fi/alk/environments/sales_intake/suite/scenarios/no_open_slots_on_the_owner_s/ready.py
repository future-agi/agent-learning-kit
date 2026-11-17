def ready(world):
    state = world.state()
    lead = next((l for l in state.get("leads", []) if l["lead_id"] == "L20"), None)
    if not lead or lead["owner_rep_id"] != "A05":
        return "L20 with owner A05 missing"
    events = [e for e in state.get("calendar_events", []) if e["calendar_id"] == "CAL-A05"]
    if len(events) < 2:
        return "Calendar events on CAL-A05 missing"
    return None
