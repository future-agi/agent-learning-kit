def ready(world):
    p = next((r for r in world.state().get("patients", []) if r["patient_id"] == "P04"), None)
    if not p:
        return "patient P04 not found"
    return None
