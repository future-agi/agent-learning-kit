def ready(world):
    orders = {o["order_id"]: o for o in world.state().get("orders", [])}
    if "O430" not in orders:
        return "Order O430 not found"
    if orders["O430"]["status"] != "processing":
        return f"Order O430 is {orders['O430']['status']}, expected processing"
    return None
