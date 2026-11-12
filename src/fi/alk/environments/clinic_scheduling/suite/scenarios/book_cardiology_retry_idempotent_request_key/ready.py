def ready(world):
    p = [x for x in world.state().get("patients", []) if x["patient_id"] == "P411"]
    if not p:
        return "P411 not found in patients"
    r = [x for x in world.state().get("referrals", []) if x["referral_id"] == "RF411" and x["status"] == "active"]
    if not r:
        return "RF411 active referral not found"
    return None
