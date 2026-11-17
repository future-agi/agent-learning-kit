def ready(world):
    state = world.state()
    threads = {t["id"] for t in state.get("gmail_threads", [])}
    if "th_c01_complaint" not in threads:
        return "th_c01_complaint missing"
    if "th_c04_distractor" not in threads:
        return "th_c04_distractor missing"
    return None
