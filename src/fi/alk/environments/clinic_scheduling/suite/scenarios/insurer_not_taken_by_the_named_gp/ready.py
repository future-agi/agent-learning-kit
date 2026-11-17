def ready(world):
    if not any(r["patient_id"] == "P420" for r in world.state()["patients"]):
        return "Patient P420 missing"
    return None
