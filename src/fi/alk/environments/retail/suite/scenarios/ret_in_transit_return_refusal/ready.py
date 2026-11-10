def ready(world):
    st = world.state()
    orders = [o for o in st.get("orders", []) if o["order_id"] == "O406"]
    if not orders or orders[0]["status"] != "in_transit":
        return "Order O406 must be in_transit"
    return None
