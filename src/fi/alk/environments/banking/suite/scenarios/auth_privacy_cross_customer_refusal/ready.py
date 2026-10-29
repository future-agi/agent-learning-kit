def ready(world):
    state = world.state()
    customers = state.get("customers", [])
    c = next((x for x in customers if x["customer_id"] == "C109"), None)
    if not c:
        return "Customer C109 must exist"
    return None
