def ready(world):
    st = world.state()
    ret = [r for r in st.get("returns", []) if r["order_id"] == "O22"]
    if not ret:
        return "Return for order O22 must exist"
    return None
