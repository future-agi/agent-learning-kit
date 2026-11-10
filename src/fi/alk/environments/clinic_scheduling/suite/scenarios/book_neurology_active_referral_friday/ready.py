def ready(world):
    p = [x for x in world.state().get("patients", []) if x["patient_id"] == "P418"]
    if not p:
        return "P418 not found in patients"
    r = [x for x in world.state().get("referrals", []) if x["referral_id"] == "RF418" and x["status"] == "active"]
    if not r:
        return "RF418 active referral not found"
    return None
