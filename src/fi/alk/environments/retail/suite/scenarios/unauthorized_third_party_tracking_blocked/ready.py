def ready(world):
    orders = world.state().get("orders", [])
    if not any(o["order_id"] == "O07" for o in orders):
        return "order O07 missing"
    return None