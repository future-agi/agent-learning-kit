def ready(world):
    st = world.state()
    ords = [o for o in st.get("orders", []) if o["order_id"] == "O440"]
    if not ords:
        return "Order O440 must exist"
    return None
