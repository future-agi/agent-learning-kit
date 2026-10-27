def ready(world):
    evts = world.state().get("events", [])
    if not any(e.get("event_id") == "EVT-CONF-99" for e in evts):
        return "EVT-CONF-99 missing"
    return None
