def ready(world):
    st = world.state()
    files = [f for f in st.get("drive_files", []) if f["file_id"] == "shipping_policy" and f["status"] == "approved"]
    if not files:
        return "Approved policy file shipping_policy not found"
    return None
