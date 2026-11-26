def ready(world):
    st = world.state()
    files = [f for f in st.get("drive_files", []) if f["file_id"] == "exchange_policy"]
    if not files:
        return "Exchange policy file missing"
    return None
