def ready(world):
    cust = next((c for c in world.state()["customers"] if c["customer_id"] == "CU445"), None)
    if not cust:
        return "CU445 missing from customers"
    return None
