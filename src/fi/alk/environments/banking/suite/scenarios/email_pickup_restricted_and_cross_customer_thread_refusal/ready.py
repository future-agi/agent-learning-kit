def ready(world):
    state = world.state()
    threads = state.get("gmail_threads", [])
    if not any(t["id"] == "th_c06_restricted_fraud" for t in threads):
        return "th_c06_restricted_fraud missing"
    return None