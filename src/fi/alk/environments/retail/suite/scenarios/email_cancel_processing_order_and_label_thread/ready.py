def ready(world):
    st = world.state()
    orders = [o for o in st.get("orders", []) if o["order_id"] == "O31" and o["status"] == "processing"]
    if not orders:
        return "Order O31 must be in processing status"
    return None
