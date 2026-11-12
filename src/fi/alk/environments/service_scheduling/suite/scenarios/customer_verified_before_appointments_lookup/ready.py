def ready(world):
    s = world.state()
    cust = next((c for c in s.get("customers", []) if c["customer_id"] == "CU412"), None)
    if not cust:
        return "customer CU412 missing"
    return None
