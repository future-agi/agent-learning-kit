def ready(world):
    state = world.state()
    p = next((r for r in state.get("patients", []) if r.get("patient_id") == 'P407'), None)
    if not p:
        return "patient P407 is missing"
    return None
