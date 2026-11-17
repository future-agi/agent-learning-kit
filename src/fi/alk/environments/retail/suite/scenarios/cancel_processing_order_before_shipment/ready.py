def ready(world):
    orders = world.state().get("orders", [])
    o = next((ord for ord in orders if ord["order_id"] == "O31"), None)
    if not o:
        return "Order O31 not found"
    if o["status"] != "processing":
        return f"Order O31 is in status {o['status']}, expected processing"
    return None