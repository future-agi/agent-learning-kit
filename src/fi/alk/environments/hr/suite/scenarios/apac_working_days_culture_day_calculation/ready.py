def ready(world):
    state = world.state()
    holidays = state.get("holiday_calendars", [])
    cul = next((h for h in holidays if h["region"] == "APAC" and h["holiday_date"] == "2026-11-03"), None)
    if not cul:
        return "APAC Culture Day on 2026-11-03 missing from holiday calendar"
    return None
