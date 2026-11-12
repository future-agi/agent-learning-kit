def ready(world):
    referrals = world.state()["referrals"]
    rf = next((r for r in referrals if r["referral_id"] == "RF04"), None)
    if not rf or rf["status"] != "active" or rf["patient_id"] != "P11":
        return f"RF04 mismatch: {rf}"
    threads = world.state()["gmail_threads"]
    th = next((t for t in threads if t["id"] == "th_c03_referral_p11"), None)
    if not th:
        return "th_c03_referral_p11 missing"
    return None
