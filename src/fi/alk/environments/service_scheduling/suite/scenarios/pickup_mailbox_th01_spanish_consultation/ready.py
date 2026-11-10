def ready(world):
    customers = world.state()["customers"]
    cust = next((c for c in customers if c["customer_id"] == "CU418"), None)
    if not cust or cust["name"] != "Valeria Gomez":
        return "Customer CU418 not ready"
    return None