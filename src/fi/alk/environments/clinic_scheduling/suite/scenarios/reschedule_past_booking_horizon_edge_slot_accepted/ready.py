def ready(world):
    state = world.state()
    if not any(a["appointment_id"] == "A14" and a["patient_id"] == "P19" and a["status"] == "scheduled" for a in state.get("appointments", [])):
        return "appointment A14 is not scheduled for P19"
    return None
