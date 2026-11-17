def ready(world):
    p = [x for x in world.state().get("patients", []) if x["patient_id"] == "P410"]
    if not p:
        return "P410 not found in patients"
    r = [x for x in world.state().get("referrals", []) if x["referral_id"] == "RF410" and x["status"] == "expired"]
    if not r:
        return "RF410 expired referral not found"
    return None
