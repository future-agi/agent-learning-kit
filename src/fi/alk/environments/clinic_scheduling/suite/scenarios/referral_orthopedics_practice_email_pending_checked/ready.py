def ready(world):
    referrals = world.state()["referrals"]
    rf = next((r for r in referrals if r["referral_id"] == "RF02"), None)
    if not rf or rf["status"] != "pending":
        return f"RF02 not pending: {rf}"
    threads = world.state()["gmail_threads"]
    th = next((t for t in threads if t["id"] == "th_c02_referral_p09"), None)
    if not th:
        return "th_c02_referral_p09 missing"
    return None
