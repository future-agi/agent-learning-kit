def ready(world):
    state = world.state()
    row = next((r for r in state.get("appointments", []) if r.get("appointment_id") == 'A02'), None)
    if not row or row.get("patient_id") != 'P04':
        return "appointments A02 is missing or its patient_id is not P04"
    return None
