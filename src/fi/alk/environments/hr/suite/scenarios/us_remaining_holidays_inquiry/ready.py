def ready(world):
    hols = [h for h in world.state().get("holiday_calendars", []) if h["region"] == "US"]
    if not hols:
        return "US holiday calendars missing"
    return None
