def ready(world):
    st = world.state()
    orders = [o for o in st.get("orders", []) if o["order_id"] in ("O470", "O471") and o["status"] == "processing"]
    if len(orders) != 2:
        return "Processing orders O470 and O471 not found in world"
    return None
