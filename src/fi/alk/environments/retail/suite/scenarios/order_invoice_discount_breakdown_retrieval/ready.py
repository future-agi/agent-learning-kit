def ready(world):
    orders = world.state().get("orders", [])
    if not any(o["order_id"] == "O18" for o in orders):
        return "order O18 missing"
    return None