def ready(world):
    p = [x for x in world.state().get("patients", []) if x["patient_id"] == "P412"]
    if not p:
        return "P412 not found in patients"
    r = [x for x in world.state().get("referrals", []) if x["referral_id"] == "RF412" and x["status"] == "active"]
    if not r:
        return "RF412 active referral not found"
    return None
