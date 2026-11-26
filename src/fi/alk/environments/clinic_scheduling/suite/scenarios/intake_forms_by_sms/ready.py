def ready(world):
    state = world.state()
    row = next((r for r in state.get("appointments", []) if r.get("appointment_id") == 'A05'), None)
    if not row or row.get("patient_id") != 'P12':
        return "appointment A05 for P12 is missing"
    return None
