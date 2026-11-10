def ready(world):
    p = [x for x in world.state().get("patients", []) if x["patient_id"] == "P401" and x["is_new_patient"]]
    if not p:
        return "P401 not found as new patient"
    return None
