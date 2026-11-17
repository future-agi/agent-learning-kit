def ready(world):
    events = world.state().get("calendar_events", [])
    if not any(e.get("event_id") == "EV-BLK-ST05-440" for e in events):
        return "missing EV-BLK-ST05-440"
    return None
