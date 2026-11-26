def ready(world):
    s = world.state()
    if not any(c["customer_id"] == "CU403" for c in s.get("customers", [])):
        return "CU403 missing"
    return None
