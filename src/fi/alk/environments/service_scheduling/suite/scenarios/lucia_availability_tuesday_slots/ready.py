def ready(world):
    cust = next((c for c in world.state().get("customers", []) if c["customer_id"] == "CU101"), None)
    if not cust:
        return "customer CU101 missing"
    return None