def ready(world):
    state = world.state()
    threads = {t["id"] for t in state.get("gmail_threads", [])}
    if "th_c02_replacement" not in threads:
        return "th_c02_replacement missing"
    return None
