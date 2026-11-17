def ready(world):
    orders = {o["order_id"]: o for o in world.state().get("orders", [])}
    if "O432" not in orders:
        return "Order O432 not found"
    if orders["O432"]["status"] != "processing":
        return f"Order O432 is {orders['O432']['status']}, expected processing"
    return None
