def ready(world):
    custs = [c for c in world.state()["customers"] if c["customer_id"] == "CU404"]
    if not custs:
        return "Customer CU404 missing"
    return None