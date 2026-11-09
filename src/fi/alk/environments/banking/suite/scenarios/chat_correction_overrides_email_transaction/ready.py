def ready(world):
    state = world.state()
    threads = {t["id"] for t in state.get("gmail_threads", [])}
    if "th_c01_complaint" not in threads:
        return "th_c01_complaint missing"
    return None
