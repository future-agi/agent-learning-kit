def ready(world):
    custs = [c for c in world.state()["customers"] if c["customer_id"] == "CU407"]
    if not custs:
        return "Customer CU407 missing"
    return None