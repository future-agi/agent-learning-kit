def ready(world):
    s = world.state()
    if not any(c["customer_id"] == "CU408" for c in s.get("customers", [])):
        return "CU408 missing"
    return None
