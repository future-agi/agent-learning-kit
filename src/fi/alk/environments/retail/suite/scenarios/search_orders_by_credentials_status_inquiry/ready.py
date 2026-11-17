def ready(world):
    orders = world.state().get("orders", [])
    if not any(o["order_id"] == "O423" for o in orders):
        return "order O423 missing"
    return None