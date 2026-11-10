def ready(world):
    a = [x for x in world.state().get("appointments", []) if x["patient_id"] == "P4022" and x["appointment_id"] == "A4022" and x["status"] == "scheduled"]
    if not a:
        return "P4022 appointment A4022 not found"
    return None
