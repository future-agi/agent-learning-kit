def ready(world):
    st = world.state()
    threads = [t for t in st.get("gmail_threads", []) if t["id"] == "th_r06_exchange_turtleneck"]
    if not threads:
        return "Thread th_r06_exchange_turtleneck missing"
    return None
