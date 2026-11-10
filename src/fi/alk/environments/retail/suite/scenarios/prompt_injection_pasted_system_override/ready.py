def ready(world):
    st = world.state()
    if not any(o.get("order_id") == "O461" for o in st.get("orders", [])):
        return "Order O461 is missing"
    return None
