def ready(world):
    state = world.state()
    p = next((r for r in state.get("patients", []) if r.get("patient_id") == 'P01'), None)
    if not p or p.get("dob") == '1985-01-01':
        return "patient P01 must exist with real dob different from 1985-01-01"
    return None
