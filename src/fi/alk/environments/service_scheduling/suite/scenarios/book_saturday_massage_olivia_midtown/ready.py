def ready(world):
    custs = [c for c in world.state()["customers"] if c["customer_id"] == "CU402"]
    if not custs:
        return "Customer CU402 missing"
    return None