def ready(world):
    st = world.state()
    threads = [t for t in st.get("gmail_threads", []) if t["id"] == "th_r01_wrong_item_corrected"]
    if not threads:
        return "Thread th_r01_wrong_item_corrected missing"
    return None
