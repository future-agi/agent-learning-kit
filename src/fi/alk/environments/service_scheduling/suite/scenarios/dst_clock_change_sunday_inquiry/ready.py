def ready(world):
    s = world.state()
    cust = next((c for c in s.get("customers", []) if c["customer_id"] == "CU411"), None)
    if not cust:
        return "customer CU411 missing"
    return None
