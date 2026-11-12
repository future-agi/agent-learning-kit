def ready(world):
    state = world.state()
    p = next((r for r in state.get("patients", []) if r.get("patient_id") == 'P20'), None)
    if not p or p.get("email"):
        return "patient P20 must exist without an email on file"
    return None
