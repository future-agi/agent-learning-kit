def ready(world):
    custs = world.state().get("customers", [])
    if not any(c.get("customer_id") == "CU449" for c in custs):
        return "missing CU449"
    return None
