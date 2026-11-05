def ready(world):
    state = world.state()
    threads = state.get("threads", [])
    if not any(t["id"] == "th_c07_ambiguous" for t in threads):
        return "th_c07_ambiguous missing"
    return None