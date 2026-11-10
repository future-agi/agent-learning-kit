def ready(world):
    customers = world.state()["customers"]
    cust = next((c for c in customers if c["customer_id"] == "CU412"), None)
    if not cust:
        return "Customer CU412 missing"
    return None