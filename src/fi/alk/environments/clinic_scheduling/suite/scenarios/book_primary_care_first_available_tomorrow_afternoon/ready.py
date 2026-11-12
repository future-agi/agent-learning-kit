def ready(world):
    p = next((r for r in world.state().get("patients", []) if r["patient_id"] == "P151"), None)
    if not p:
        return "patient P151 not found"
    return None
