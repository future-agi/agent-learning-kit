def ready(world):
    p = [x for x in world.state().get("patients", []) if x["patient_id"] == "P406"]
    if not p:
        return "P406 not found in patients"
    return None
