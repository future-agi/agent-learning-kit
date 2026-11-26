def ready(world):
    st = world.state()
    files = [f for f in st.get("drive_files", []) if f["file_id"] == "ret_pol_v3" and f["status"] == "approved"]
    if not files:
        return "ret_pol_v3 not found or not approved"
    return None
