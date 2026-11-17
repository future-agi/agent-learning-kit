def ready(world):
    st = world.state()
    orders = [o for o in st.get("orders", []) if o["order_id"] == "O474" and o["status"] == "processing"]
    if not orders:
        return "Processing order O474 not found"
    return None
