def ready(world):
    state = world.state()
    thread = next((t for t in state.get("gmail_threads", []) if t["id"] == "th_b06_collections"), None)
    if not thread:
        return "thread th_b06_collections missing"
    if not thread.get("is_restricted"):
        return "thread th_b06_collections should be marked restricted"
    return None
