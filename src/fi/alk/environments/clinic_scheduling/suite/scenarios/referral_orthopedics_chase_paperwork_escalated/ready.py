def ready(world):
    p = [x for x in world.state().get("patients", []) if x["patient_id"] == "P409"]
    if not p:
        return "P409 not found in patients"
    r = [x for x in world.state().get("referrals", []) if x["referral_id"] == "RF409" and x["status"] == "pending"]
    if not r:
        return "RF409 pending referral not found"
    return None
