def ready(world):
    s = world.state()
    if not any(c["customer_id"] == "CU411" for c in s.get("customers", [])):
        return "CU411 missing"
    return None
