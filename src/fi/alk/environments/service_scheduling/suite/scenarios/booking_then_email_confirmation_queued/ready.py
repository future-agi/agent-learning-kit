def ready(world):
    s = world.state()
    if not any(c["customer_id"] == "CU414" for c in s.get("customers", [])):
        return "CU414 missing"
    return None
