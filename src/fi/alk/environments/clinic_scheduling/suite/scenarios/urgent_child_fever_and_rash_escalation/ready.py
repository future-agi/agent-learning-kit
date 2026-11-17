def ready(world):
    state = world.state()
    p = next((r for r in state.get("patients", []) if r.get("patient_id") == 'P405'), None)
    if not p:
        return "patient P405 is missing"
    return None
