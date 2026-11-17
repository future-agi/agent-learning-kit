def ready(world):
    st = world.state()
    orders = [o for o in st.get("orders", []) if o["order_id"] == "O472" and o["status"] == "delivered"]
    if not orders:
        return "Delivered order O472 not found"
    return None
