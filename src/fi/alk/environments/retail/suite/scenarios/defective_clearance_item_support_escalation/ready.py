def ready(world):
    orders = world.state().get("orders", [])
    if not any(o["order_id"] == "O422" for o in orders):
        return "order O422 missing"
    return None