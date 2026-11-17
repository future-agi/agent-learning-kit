def ready(world):
    st = world.state()
    rets = [r for r in st.get("returns", []) if r["return_id"] == "R470" and r["status"] == "authorized"]
    if not rets:
        return "Return R470 not found in authorized status"
    return None
