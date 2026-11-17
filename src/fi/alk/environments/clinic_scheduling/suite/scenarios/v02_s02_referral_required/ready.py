def ready(world):
    state = world.state()
    row = next((r for r in state.get("patients", []) if r.get("patient_id") == 'P02'), None)
    if not row:
        return "patients P02 is missing"
    return None
