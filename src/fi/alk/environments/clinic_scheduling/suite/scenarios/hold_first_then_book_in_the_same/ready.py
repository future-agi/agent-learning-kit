def ready(world):
    if not any(r["patient_id"] == "P421" for r in world.state()["patients"]):
        return "Patient P421 missing"
    return None
