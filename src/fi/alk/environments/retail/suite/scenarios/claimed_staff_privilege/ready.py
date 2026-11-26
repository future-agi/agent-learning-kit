def ready(world):
    st = world.state()
    orders = [o for o in st.get("orders", []) if o["order_id"] == "O475" and o["status"] == "delivered"]
    if not orders:
        return "Delivered order O475 not found"
    return None
