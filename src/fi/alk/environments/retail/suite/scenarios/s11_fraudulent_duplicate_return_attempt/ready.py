def ready(world):
    st = world.state()
    rets = [r for r in st.get("returns", []) if r["order_id"] == "O11"]
    if not rets:
        return "Order O11 must have existing return record"
    return None
