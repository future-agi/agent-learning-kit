def ready(world):
    s = world.state()
    l = next((loc for loc in s.get("locations", []) if loc["location_id"] == "L2"), None)
    if not l:
        return "L2 location not found"
    c = next((cust for cust in s.get("customers", []) if cust["customer_id"] == "CU401"), None)
    if not c:
        return "CU401 customer not found"
    return None