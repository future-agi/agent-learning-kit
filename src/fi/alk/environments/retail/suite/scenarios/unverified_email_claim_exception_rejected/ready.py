def ready(world):
    st = world.state()
    if not any(o.get("order_id") == "O403" for o in st.get("orders", [])):
        return "Order O403 is missing"
    return None
