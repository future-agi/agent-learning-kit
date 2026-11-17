def ready(world):
    s = world.state()
    c = next((cust for cust in s.get("customers", []) if cust["customer_id"] == "CU406"), None)
    if not c:
        return "CU406 customer missing"
    return None