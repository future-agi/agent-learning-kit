def ready(world):
    patients = {p["patient_id"]: p for p in world.state().get("patients", [])}
    if "P413" not in patients:
        return "patient P413 missing"
    return None