def ready(world):
    orders = {o["order_id"]: o for o in world.state().get("orders", [])}
    if "O434" not in orders:
        return "Order O434 not found"
    if orders["O434"]["status"] != "delivered":
        return f"Order O434 is {orders['O434']['status']}, expected delivered"
    return None
