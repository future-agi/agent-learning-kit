def ready(world):
    st = world.state()
    threads = [t for t in st.get("gmail_threads", []) if t["subject"] == "Return request"]
    if len(threads) < 2:
        return "Need at least two threads with subject 'Return request'"
    return None
