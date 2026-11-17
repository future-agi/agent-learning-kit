def ready(world):
    state = world.state()
    threads = {t["id"] for t in state.get("gmail_threads", [])}
    if "th_c08_second_request" not in threads:
        return "th_c08_second_request missing"
    return None
