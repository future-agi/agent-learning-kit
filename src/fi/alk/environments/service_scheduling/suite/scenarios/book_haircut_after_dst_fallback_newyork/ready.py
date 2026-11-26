def ready(world):
    custs = [c for c in world.state()["customers"] if c["customer_id"] == "CU405"]
    if not custs:
        return "Customer CU405 missing"
    return None