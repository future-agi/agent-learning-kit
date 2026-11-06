def ready(world):
    state = world.state()
    threads = state.get("threads", [])
    if not any(t["id"] == "th_c01_complaint" for t in threads):
        return "th_c01_complaint missing"
    return None