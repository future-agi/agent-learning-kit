def ready(world):
    orders = world.state().get("orders", [])
    if not any(o["order_id"] == "O420" for o in orders):
        return "order O420 not found in database"
    return None
