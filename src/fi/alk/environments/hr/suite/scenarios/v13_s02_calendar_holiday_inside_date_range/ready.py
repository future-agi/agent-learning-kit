def ready(world):
    state = world.state()
    holidays = state.get("holiday_calendars", [])
    vet_day = next((h for h in holidays if h["region"] == "US" and h["holiday_date"] == "2026-11-11"), None)
    if not vet_day:
        return "US Veterans Day holiday on 2026-11-11 missing"
    return None
