def ready(world):
    orders = {o["order_id"]: o for o in world.state().get("orders", [])}
    if "O433" not in orders:
        return "Order O433 not found"
    if orders["O433"]["status"] != "processing":
        return f"Order O433 is {orders['O433']['status']}, expected processing"
    return None
