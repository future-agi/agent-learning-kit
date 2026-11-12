def ready(world):
    p = [x for x in world.state().get("patients", []) if x["patient_id"] == "P414"]
    if not p:
        return "P414 not found in patients"
    h = [x for x in world.state().get("slot_holds", []) if x["hold_id"] == "H01" and x["status"] == "active"]
    if not h:
        return "H01 active hold not found"
    return None
