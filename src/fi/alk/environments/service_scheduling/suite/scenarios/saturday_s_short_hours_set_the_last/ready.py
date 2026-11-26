def ready(world):
    custs = world.state().get("customers", [])
    if not any(c.get("customer_id") == "CU441" for c in custs):
        return "missing CU441"
    return None
