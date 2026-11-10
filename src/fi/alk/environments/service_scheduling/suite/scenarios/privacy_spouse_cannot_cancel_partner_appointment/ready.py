def ready(world):
    s = world.state()
    c = next((cust for cust in s.get("customers", []) if cust["customer_id"] == "CU411"), None)
    if not c:
        return "CU411 customer missing"
    return None