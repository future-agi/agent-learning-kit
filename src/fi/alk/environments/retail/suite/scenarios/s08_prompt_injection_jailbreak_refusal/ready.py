def ready(world):
    st = world.state()
    ords = [o for o in st.get("orders", []) if o["order_id"] == "O08"]
    if not ords:
        return "Order O08 must exist"
    return None
