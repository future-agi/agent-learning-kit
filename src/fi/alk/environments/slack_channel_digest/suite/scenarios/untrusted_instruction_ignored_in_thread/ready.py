def ready(world):
    st = world.state()
    msgs = [m for m in st.get("messages", []) if m.get("channel_id") == "C01" and m.get("thread_ts") == "1729850000.000100"]
    if len(msgs) < 2:
        return "expected thread with messages in C01"
    return None
