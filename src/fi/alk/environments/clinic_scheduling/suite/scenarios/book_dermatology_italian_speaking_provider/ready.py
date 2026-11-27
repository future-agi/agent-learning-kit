def ready(world):
    p = next((r for r in world.state().get("patients", []) if r["patient_id"] == "P131"), None)
    if not p:
        return "patient P131 not found"
    return None
