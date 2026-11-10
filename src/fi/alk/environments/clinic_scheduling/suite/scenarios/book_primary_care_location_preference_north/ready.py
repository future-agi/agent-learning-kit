def ready(world):
    p = next((r for r in world.state().get("patients", []) if r["patient_id"] == "P101"), None)
    if not p:
        return "patient P101 not found"
    return None
