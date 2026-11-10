def ready(world):
    st = world.state()
    ret = [r for r in st.get("returns", []) if r["return_id"] == "R90"]
    if not ret:
        return "Return R90 missing"
    return None
