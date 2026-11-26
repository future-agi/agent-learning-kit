def ready(world):
    s = world.state()
    if not any(c["customer_id"] == "CU410" for c in s.get("customers", [])):
        return "CU410 missing"
    return None
