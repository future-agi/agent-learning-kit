def ready(world):
    s = world.state()
    if not any(c["customer_id"] == "CU413" for c in s.get("customers", [])):
        return "CU413 missing"
    if not any(w["waitlist_id"] == "W401" and w["customer_id"] == "CU413" for w in s.get("waitlist", [])):
        return "W401 missing"
    return None
