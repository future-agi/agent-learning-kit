def ready(world):
    state = world.state()
    p = next((r for r in state.get("patients", []) if r.get("patient_id") == 'P408'), None)
    if not p:
        return "patient P408 is missing"
    return None
