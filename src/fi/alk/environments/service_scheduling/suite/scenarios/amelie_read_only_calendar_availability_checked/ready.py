def ready(world):
    s = world.state()
    if not any(c["customer_id"] == "CU406" for c in s.get("customers", [])):
        return "CU406 missing"
    return None
