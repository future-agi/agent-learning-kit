def ready(world):
    st = world.state()
    threads = [t for t in st.get("gmail_threads", []) if t["id"] == "th_r04_return_request_rachel"]
    if not threads:
        return "Thread th_r04_return_request_rachel missing"
    return None
