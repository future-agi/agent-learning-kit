def ready(world):
    state = world.state()
    row = next((r for r in state.get("appointments", []) if r.get("appointment_id") == 'A401'), None)
    if not row or row.get("patient_id") != 'P401':
        return "appointment A401 for P401 is missing"
    return None
