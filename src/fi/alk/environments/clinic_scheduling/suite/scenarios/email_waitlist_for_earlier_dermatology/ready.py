def ready(world):
    state = world.state()
    row = next((r for r in state.get("appointments", []) if r.get("appointment_id") == 'A10'), None)
    if not row or row.get("status") != 'scheduled':
        return "appointments A10 is missing or its status is not scheduled"
    return None
