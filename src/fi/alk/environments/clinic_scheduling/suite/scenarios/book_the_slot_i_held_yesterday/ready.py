def ready(world):
    if not any(r["patient_id"] == "P13" for r in world.state()["patients"]):
        return "Patient P13 missing"
    if not any(h["hold_id"] == "H02" and h["status"] == "expired" for h in world.state()["slot_holds"]):
        return "Expired hold H02 missing"
    return None
