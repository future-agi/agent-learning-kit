def ready(world):
    referrals = world.state()["referrals"]
    rf = next((r for r in referrals if r["referral_id"] == "RF03"), None)
    if not rf or rf["status"] != "expired" or rf["patient_id"] != "P10":
        return f"RF03 expired mismatch: {rf}"
    return None
