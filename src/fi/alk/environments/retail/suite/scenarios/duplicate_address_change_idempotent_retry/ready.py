def ready(world):
    st = world.state()
    ords = [o for o in st.get("orders", []) if o["order_id"] == "O451"]
    if not ords or ords[0]["status"] != "processing":
        return "Order O451 must exist and be processing"
    return None
