def ready(world):
    s = world.state()
    if not any(c["customer_id"] == "CU409" for c in s.get("customers", [])):
        return "CU409 missing"
    return None
