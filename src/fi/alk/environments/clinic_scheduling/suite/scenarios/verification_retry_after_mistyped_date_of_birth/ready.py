def ready(world):
    state = world.state()
    if not any(a["appointment_id"] == "A15" and a["patient_id"] == "P07" and a["status"] == "scheduled" for a in state.get("appointments", [])):
        return "appointment A15 is not scheduled for P07"
    return None
