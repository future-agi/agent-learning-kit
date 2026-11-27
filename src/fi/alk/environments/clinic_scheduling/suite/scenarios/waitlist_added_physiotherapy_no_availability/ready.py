def ready(world):
    patients = {p["patient_id"]: p for p in world.state().get("patients", [])}
    if "P410" not in patients:
        return "patient P410 missing"
    return None