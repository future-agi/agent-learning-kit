def ready(world):
    patients = {p["patient_id"]: p for p in world.state().get("patients", [])}
    appts = {a["appointment_id"]: a for a in world.state().get("appointments", [])}
    if "P04" not in patients or "A10" not in appts:
        return "patient P04 or appointment A10 missing"
    if appts["A10"]["patient_id"] == "P04":
        return "A10 should not belong to P04"
    return None