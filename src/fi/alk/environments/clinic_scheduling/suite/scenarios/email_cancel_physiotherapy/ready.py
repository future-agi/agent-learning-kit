def ready(world):
    state = world.state()
    row = next((r for r in state.get("appointments", []) if r.get("appointment_id") == 'A09'), None)
    if not row or row.get("status") != 'scheduled':
        return "appointments A09 is missing or its status is not scheduled"
    return None
