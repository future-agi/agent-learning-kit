def ready(world):
    s = world.state()
    if not any(c["customer_id"] == "CU405" for c in s.get("customers", [])):
        return "CU405 missing"
    if not any(t["id"] == "th_s402_full_day_waitlist" for t in s.get("gmail_threads", [])):
        return "th_s402_full_day_waitlist missing"
    return None
