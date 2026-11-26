def ready(world):
    customers = world.state()["customers"]
    cust = next((c for c in customers if c["customer_id"] == "CU411"), None)
    if not cust:
        return "Customer CU411 missing"
    return None