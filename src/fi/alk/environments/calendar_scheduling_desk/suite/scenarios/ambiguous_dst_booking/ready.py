def ready(world):
    cals = world.state().get("calendars", [])
    if not any(c.get("calendar_id") == "CAL-PRIMARY" for c in cals):
        return "CAL-PRIMARY missing"
    return None
