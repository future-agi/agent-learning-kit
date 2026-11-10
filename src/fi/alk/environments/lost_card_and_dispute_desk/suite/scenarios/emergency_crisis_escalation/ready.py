def ready(world):
    state = world.state()
    customers = state.get("customers", [])
    c = next((x for x in customers if x["customer_id"] == "C114"), None)
    if not c:
        return "Customer C114 must exist"
    return None
