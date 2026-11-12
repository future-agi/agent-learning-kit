def ready(world):
    s = world.state()
    c = next((cust for cust in s.get("customers", []) if cust["customer_id"] == "CU415"), None)
    if not c:
        return "CU415 customer missing"
    return None