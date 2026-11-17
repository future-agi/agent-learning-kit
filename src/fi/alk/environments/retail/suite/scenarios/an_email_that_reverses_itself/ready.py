def ready(world):
    st = world.state()
    threads = [t for t in st.get("gmail_threads", []) if t["id"] == "th_r470_cancel_then_keep"]
    if not threads:
        return "Thread th_r470_cancel_then_keep not found"
    return None
