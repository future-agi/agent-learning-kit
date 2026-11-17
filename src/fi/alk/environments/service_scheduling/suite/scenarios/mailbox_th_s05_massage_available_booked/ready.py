def ready(world):
    s = world.state()
    if not any(c["customer_id"] == "CU416" for c in s.get("customers", [])):
        return "CU416 missing"
    if not any(t["id"] == "th_s403_callum_massage" for t in s.get("gmail_threads", [])):
        return "th_s403_callum_massage missing"
    return None
