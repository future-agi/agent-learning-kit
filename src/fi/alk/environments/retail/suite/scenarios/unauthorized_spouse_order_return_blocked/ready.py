def ready(world):
    st = world.state()
    if not any(o.get("order_id") == "O452" for o in st.get("orders", [])):
        return "Order O452 is missing"
    return None
