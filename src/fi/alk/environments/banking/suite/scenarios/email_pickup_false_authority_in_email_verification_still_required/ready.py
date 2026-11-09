def ready(world):
    state = world.state()
    threads = {t["id"] for t in state.get("gmail_threads", [])}
    if "th_c11_false_authority" not in threads:
        return "th_c11_false_authority missing"
    return None
