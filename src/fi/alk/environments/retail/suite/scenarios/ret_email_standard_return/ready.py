def ready(world):
    st = world.state()
    threads = [t for t in st.get("gmail_threads", []) if t["id"] == "th_r03_return_request_bob"]
    if not threads:
        return "Thread th_r03_return_request_bob missing"
    return None
