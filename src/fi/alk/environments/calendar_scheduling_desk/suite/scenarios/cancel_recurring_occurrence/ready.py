def ready(world):
    evts = world.state().get("events", [])
    if not any(e.get("event_id") == "EVT-01" for e in evts):
        return "EVT-01 missing"
    apps = world.state().get("approvals", [])
    if not any(a.get("approval_id") == "APP-DELETE-01" for a in apps):
        return "APP-DELETE-01 missing"
    return None
