def ready(world):
    p = [x for x in world.state().get("patients", []) if x["patient_id"] == "P402"]
    if not p:
        return "P402 not found in patients"
    r = [x for x in world.state().get("referrals", []) if x["referral_id"] == "RF402" and x["status"] == "requested"]
    if not r:
        return "RF402 requested referral not found"
    return None
