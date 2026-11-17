def ready(world):
    st = world.state()
    ret = [r for r in st.get("returns", []) if r["order_id"] == "O11"]
    if not ret:
        return "Return for order O11 must exist"
    return None
