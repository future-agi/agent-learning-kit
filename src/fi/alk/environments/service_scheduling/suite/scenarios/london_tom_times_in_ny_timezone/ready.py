def ready(world):
    s = world.state()
    if not any(c["customer_id"] == "CU402" for c in s.get("customers", [])):
        return "CU402 missing"
    return None
