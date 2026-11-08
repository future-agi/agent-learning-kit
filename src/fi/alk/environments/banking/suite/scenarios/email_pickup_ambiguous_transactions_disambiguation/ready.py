def ready(world):
    state = world.state()
    threads = state.get("gmail_threads", [])
    if not any(t["id"] == "th_c07_ambiguous" for t in threads):
        return "th_c07_ambiguous missing"
    return None