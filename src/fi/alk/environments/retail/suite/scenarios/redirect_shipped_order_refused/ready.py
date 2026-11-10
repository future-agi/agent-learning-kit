def ready(world):
    orders = {o["order_id"]: o for o in world.state().get("orders", [])}
    if "O407" not in orders:
        return "Order O407 not found"
    if orders["O407"]["status"] != "in_transit":
        return f"Order O407 is {orders['O407']['status']}, expected in_transit"
    return None
