def ready(world):
    referrals = world.state()["referrals"]
    rf = next((r for r in referrals if r["referral_id"] == "RF21"), None)
    if not rf:
        return "RF21 missing"
    if rf["status"] != "pending" or rf["patient_id"] != "P21":
        return f"RF21 status/patient mismatch: {rf}"
    return None
