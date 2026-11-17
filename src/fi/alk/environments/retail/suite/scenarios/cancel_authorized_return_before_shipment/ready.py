def ready(world):
    st = world.state()
    ret = [r for r in st.get("returns", []) if r["return_id"] == "R451"]
    if not ret or ret[0]["status"] != "authorized":
        return "Return R451 must exist and be authorized"
    return None
