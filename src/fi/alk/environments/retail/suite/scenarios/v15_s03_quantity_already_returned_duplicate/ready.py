def ready(world):
    st = world.state()
    rets = [r for r in st.get("returns", []) if r["order_id"] == "O02"]
    if not rets:
        return "Order O02 must have existing return record"
    return None
