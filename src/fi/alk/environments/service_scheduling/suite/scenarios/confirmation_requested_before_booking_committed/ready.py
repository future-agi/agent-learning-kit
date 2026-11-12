def ready(world):
    s = world.state()
    if not any(c["customer_id"] == "CU412" for c in s.get("customers", [])):
        return "CU412 missing"
    return None
