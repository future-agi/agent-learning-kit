def ready(world):
    p = [x for x in world.state().get("patients", []) if x["patient_id"] == "P407"]
    if not p:
        return "P407 not found in patients"
    return None
