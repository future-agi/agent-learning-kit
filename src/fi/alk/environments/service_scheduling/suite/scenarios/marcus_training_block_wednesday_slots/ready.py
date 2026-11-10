def ready(world):
    s = world.state()
    if not any(c["customer_id"] == "CU401" for c in s.get("customers", [])):
        return "CU401 missing"
    return None
