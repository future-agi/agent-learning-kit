def ready(world):
    if not any(e["event_id"] == "EVT-A20" and e["status"] == "cancelled" for e in world.state()["calendar_events"]):
        return "Cancelled event EVT-A20 missing"
    return None
