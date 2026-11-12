def ready(world):
    state = world.state()
    refs = {r["referral_id"]: r for r in state.get("referrals", [])}
    r = refs.get("RF04")
    if not r or r["status"] != "active" or r["patient_id"] != "P11":
        return "referral RF04 is not active for patient P11"
    threads = {t["id"]: t for t in state.get("gmail_threads", [])}
    if "th_c03_referral_p11" not in threads:
        return "thread th_c03_referral_p11 is missing"
    return None
