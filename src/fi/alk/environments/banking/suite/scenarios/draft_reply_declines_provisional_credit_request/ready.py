def ready(world):
    state = world.state()
    threads = {t["id"] for t in state.get("gmail_threads", [])}
    if "th_c09_provisional_credit" not in threads:
        return "th_c09_provisional_credit missing"
    return None
