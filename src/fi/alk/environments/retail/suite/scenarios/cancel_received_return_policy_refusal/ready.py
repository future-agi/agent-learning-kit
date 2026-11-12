def ready(world):
    st = world.state()
    ret = [r for r in st.get("returns", []) if r["return_id"] == "R90"]
    if not ret or ret[0]["status"] != "received":
        return "Return R90 must exist and have status 'received'"
    return None
