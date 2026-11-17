def ready(world):
    customers = world.state()["customers"]
    cust = next((c for c in customers if c["customer_id"] == "CU419"), None)
    if not cust:
        return "Customer CU419 missing"
    return None