def ready(world):
    cust = next((c for c in world.state()["customers"] if c["customer_id"] == "CU446"), None)
    if not cust:
        return "CU446 missing from customers"
    return None
