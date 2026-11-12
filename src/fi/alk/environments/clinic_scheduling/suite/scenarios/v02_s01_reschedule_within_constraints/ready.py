def ready(world):
    state = world.state()
    row = next((r for r in state.get("appointments", []) if r.get("appointment_id") == 'A01'), None)
    if not row or row.get("status") != 'scheduled':
        return "appointments A01 is missing or its status is not scheduled"
    return None
