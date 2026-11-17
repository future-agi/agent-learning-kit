def ready(world):
    st = world.state()
    threads = [t for t in st.get("gmail_threads", []) if t["id"] == "th_r14_where_is_order"]
    if not threads:
        return "Thread th_r14_where_is_order missing"
    return None
