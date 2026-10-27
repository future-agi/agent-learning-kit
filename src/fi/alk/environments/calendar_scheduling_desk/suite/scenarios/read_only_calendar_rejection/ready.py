def ready(world):
    evts = world.state().get("events", [])
    if not any(e.get("event_id") == "EVT-MOVE-01" for e in evts):
        return "EVT-MOVE-01 missing"
    cals = world.state().get("calendars", [])
    cal_ro = next((c for c in cals if c.get("calendar_id") == "CAL-RO"), None)
    if not cal_ro or cal_ro.get("access_role") != "reader":
        return "CAL-RO is not reader role"
    return None
