def ready(world):
    evts = world.state().get("events", [])
    if not any(e.get("event_id") == "EVT-RESCHED-01" for e in evts):
        return "EVT-RESCHED-01 missing"
    if not any(e.get("event_id") == "EVT-BLOCK-02" for e in evts):
        return "EVT-BLOCK-02 missing"
    return None
