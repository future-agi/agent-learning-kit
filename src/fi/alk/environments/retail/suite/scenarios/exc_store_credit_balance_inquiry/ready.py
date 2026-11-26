def ready(world):
    st = world.state()
    threads = [t for t in st.get("gmail_threads", []) if t["id"] == "th_r13_store_credit"]
    if not threads:
        return "Thread th_r13_store_credit missing"
    return None
