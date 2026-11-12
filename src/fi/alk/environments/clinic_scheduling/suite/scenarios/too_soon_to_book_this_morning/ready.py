def ready(world):
    if not any(r["patient_id"] == "P06" for r in world.state()["patients"]):
        return "Patient P06 missing"
    return None
