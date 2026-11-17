def ready(world):
    orders = world.state().get("orders", [])
    if not any(o["order_id"] == "O14" for o in orders):
        return "order O14 not found"
    return None
