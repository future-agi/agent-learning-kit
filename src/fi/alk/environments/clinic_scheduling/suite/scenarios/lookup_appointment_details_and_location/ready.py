def ready(world):
    state = world.state()
    row = next((r for r in state.get("appointments", []) if r.get("appointment_id") == 'A403'), None)
    if not row or row.get("patient_id") != 'P403':
        return "appointment A403 for P403 is missing"
    return None
