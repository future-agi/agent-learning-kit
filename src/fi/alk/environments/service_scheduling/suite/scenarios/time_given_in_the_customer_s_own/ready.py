def ready(world):
    cust = next((c for c in world.state()["customers"] if c["customer_id"] == "CU442"), None)
    if not cust:
        return "CU442 missing from customers"
    return None
