def ready(world):
    state = world.state()
    threads = {t["id"] for t in state.get("gmail_threads", [])}
    if "th_c11_general" not in threads:
        return "th_c11_general missing"
    return None
