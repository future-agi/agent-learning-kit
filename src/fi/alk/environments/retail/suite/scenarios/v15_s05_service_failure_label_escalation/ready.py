def ready(world):
    st = world.state()
    rets = [r for r in st.get("returns", []) if r["return_id"] == "R01" and r["order_id"] == "O05"]
    if not rets:
        return "Return R01 must exist for order O05"
    return None
