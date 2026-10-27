def ready(world):
    state = world.state()
    customers = state.get("customers", [])
    c = next((x for x in customers if x["customer_id"] == "C115"), None)
    if not c:
        return "Customer C115 must exist"
    return None
