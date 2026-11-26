def ready(world):
    cust = next((c for c in world.state()["customers"] if c["customer_id"] == "CU448"), None)
    if not cust:
        return "CU448 missing from customers"
    return None
