def ready(world):
    custs = [c for c in world.state()["customers"] if c["customer_id"] == "CU401"]
    if not custs:
        return "Customer CU401 missing from customers"
    return None