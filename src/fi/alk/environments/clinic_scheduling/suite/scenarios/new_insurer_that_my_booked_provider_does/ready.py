def ready(world):
    if not any(p["patient_id"] == "P12" for p in world.state()["patients"]):
        return "Patient P12 missing"
    if not any(a["appointment_id"] == "A05" for a in world.state()["appointments"]):
        return "Appointment A05 missing"
    return None
