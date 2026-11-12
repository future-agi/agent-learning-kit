def ready(world):
    p = [x for x in world.state().get("patients", []) if x["patient_id"] == "P14"]
    if not p:
        return "P14 not found in patients"
    return None
