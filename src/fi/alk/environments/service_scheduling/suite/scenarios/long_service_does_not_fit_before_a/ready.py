def ready(world):
    cust = next((c for c in world.state()["customers"] if c["customer_id"] == "CU447"), None)
    if not cust:
        return "CU447 missing from customers"
    return None
