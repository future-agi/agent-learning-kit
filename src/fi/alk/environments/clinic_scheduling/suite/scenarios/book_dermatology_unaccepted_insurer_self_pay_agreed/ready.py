def ready(world):
    p = next((r for r in world.state().get("patients", []) if r["patient_id"] == "P161"), None)
    if not p:
        return "patient P161 not found"
    return None
