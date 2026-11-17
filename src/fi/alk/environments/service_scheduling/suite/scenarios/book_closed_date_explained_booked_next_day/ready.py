def ready(world):
    customers = world.state()["customers"]
    cust = next((c for c in customers if c["customer_id"] == "CU409"), None)
    if not cust:
        return "Customer CU409 missing"
    return None