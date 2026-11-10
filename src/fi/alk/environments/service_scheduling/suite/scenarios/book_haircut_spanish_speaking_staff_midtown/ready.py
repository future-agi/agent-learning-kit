def ready(world):
    custs = [c for c in world.state()["customers"] if c["customer_id"] == "CU406"]
    if not custs:
        return "Customer CU406 missing"
    return None