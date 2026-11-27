def ready(world):
    hols = world.state()["holiday_calendars"]
    uk_hols = [h for h in hols if h["region"] == "UK"]
    if len(uk_hols) < 2:
        return "UK holidays missing in holiday_calendars"
    return None
