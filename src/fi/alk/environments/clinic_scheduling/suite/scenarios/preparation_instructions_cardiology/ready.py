def ready(world):
    state = world.state()
    row = next((r for r in state.get("appointments", []) if r.get("appointment_id") == 'A03'), None)
    if not row or row.get("patient_id") != 'P08' or row.get("specialty") != 'cardiology':
        return "appointment A03 for P08 cardiology is missing"
    return None
