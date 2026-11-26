def ready(world):
    p = [x for x in world.state().get("patients", []) if x["patient_id"] == "P405"]
    if not p:
        return "P405 not found in patients"
    t = [x for x in world.state().get("gmail_threads", []) if x["id"] == "th_c405_intake"]
    if not t:
        return "th_c405_intake not found in gmail_threads"
    return None
