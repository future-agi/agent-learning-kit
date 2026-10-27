def ready(world):
    st = world.state()
    ords = [o for o in st.get("orders", []) if o["order_id"] == "O12"]
    if not ords:
        return "Order O12 must exist"
    return None
