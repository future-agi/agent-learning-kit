def ready(world):
    cust = next((c for c in world.state().get("customers", []) if c["customer_id"] == "CU102"), None)
    if not cust:
        return "customer CU102 missing"
    return None