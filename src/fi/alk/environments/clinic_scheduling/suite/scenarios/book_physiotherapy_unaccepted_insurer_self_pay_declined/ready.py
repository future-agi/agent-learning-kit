def ready(world):
    p = next((r for r in world.state().get("patients", []) if r["patient_id"] == "P08"), None)
    if not p:
        return "patient P08 not found"
    return None
