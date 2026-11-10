def ready(world):
    custs = world.state().get("customers", [])
    if not any(c.get("customer_id") == "CU443" for c in custs):
        return "missing CU443"
    return None
