def ready(world):
    state = world.state()
    threads = {t["id"] for t in state.get("gmail_threads", [])}
    if "th_c07_double_correction" not in threads:
        return "th_c07_double_correction missing"
    return None
