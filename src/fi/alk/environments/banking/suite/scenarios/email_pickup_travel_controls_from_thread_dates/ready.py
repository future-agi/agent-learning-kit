def ready(world):
    state = world.state()
    threads = {t["id"] for t in state.get("gmail_threads", [])}
    if "th_c12_travel_controls" not in threads:
        return "th_c12_travel_controls missing"
    return None
