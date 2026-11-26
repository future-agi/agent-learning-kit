def ready(world):
    st = world.state()
    if not any(t.get("id") == "th_r407_robert_return" for t in st.get("gmail_threads", [])):
        return "Thread th_r407_robert_return is missing"
    return None
