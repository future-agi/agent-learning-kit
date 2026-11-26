def ready(world):
    if not any(p["patient_id"] == "P05" for p in world.state()["patients"]):
        return "Patient P05 missing"
    return None
