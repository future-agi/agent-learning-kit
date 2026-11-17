def ready(world):
    st = world.state()
    threads = [t for t in st.get("gmail_threads", []) if t["id"] == "th_r09_window_question"]
    if not threads:
        return "Thread th_r09_window_question missing"
    return None
