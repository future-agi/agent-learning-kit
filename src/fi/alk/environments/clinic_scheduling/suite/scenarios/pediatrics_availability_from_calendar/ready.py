def ready(world):
    state = world.state()
    row = next((r for r in state.get("calendar_calendars", []) if r.get("calendar_id") == 'CAL-D06'), None)
    if not row:
        return "calendar_calendars CAL-D06 is missing"
    return None
