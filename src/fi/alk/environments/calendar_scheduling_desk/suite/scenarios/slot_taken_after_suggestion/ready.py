def ready(world):
    evts = world.state().get("calendar_events", [])
    if not any(e.get("event_id") == "EVT-CONF-99" for e in evts):
        return "EVT-CONF-99 missing"
    return None
