def ready(world):
    orders = world.state().get("orders", [])
    if not any(o["order_id"] == "O07" and o["status"] == "in_transit" for o in orders):
        return "order O07 not found or not in_transit"
    return None
