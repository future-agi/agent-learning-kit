def ready(world):
    p = [x for x in world.state().get("patients", []) if x["patient_id"] == "P4012"]
    if not p:
        return "P4012 not found in patients"
    return None
