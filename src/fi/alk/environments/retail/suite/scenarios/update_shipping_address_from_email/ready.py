def ready(world):
    orders = world.state().get("orders", [])
    if not any(o["order_id"] == "O32" and o["status"] == "processing" for o in orders):
        return "processing order O32 missing"
    return None