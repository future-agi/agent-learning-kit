def ready(world):
    custs = [c for c in world.state()["customers"] if c["customer_id"] == "CU403"]
    if not custs:
        return "Customer CU403 missing"
    return None