def ready(world):
    s = world.state()
    if not any(c["customer_id"] == "CU407" for c in s.get("customers", [])):
        return "CU407 missing"
    return None
