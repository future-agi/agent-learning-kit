def ready(world):
    customers = world.state()["customers"]
    cust = next((c for c in customers if c["customer_id"] == "CU410"), None)
    if not cust:
        return "Customer CU410 missing"
    return None