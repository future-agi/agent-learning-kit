def ready(world):
    st = world.state()
    ords = [o for o in st.get("orders", []) if o["order_id"] == "O450"]
    if not ords:
        return "Order O450 must exist"
    return None
