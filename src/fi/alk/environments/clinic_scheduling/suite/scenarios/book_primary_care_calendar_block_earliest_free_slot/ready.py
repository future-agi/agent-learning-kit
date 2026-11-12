def ready(world):
    p = [x for x in world.state().get("patients", []) if x["patient_id"] == "P4011"]
    if not p:
        return "P4011 not found"
    e = [x for x in world.state().get("calendar_events", []) if x["event_id"] == "BLK-01" and x["calendar_id"] == "CAL-D02"]
    if not e:
        return "BLK-01 block on CAL-D02 not found"
    return None
