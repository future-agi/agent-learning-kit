def ready(world):
    customers = world.state()["customers"]
    cust = next((c for c in customers if c["customer_id"] == "CU408"), None)
    if not cust:
        return "Customer CU408 missing"
    return None