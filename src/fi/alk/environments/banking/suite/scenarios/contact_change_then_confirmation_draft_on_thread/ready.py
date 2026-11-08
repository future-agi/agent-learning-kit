def ready(world):
    state = world.state()
    threads = {t["id"] for t in state.get("gmail_threads", [])}
    if "th_c05_address_change" not in threads:
        return "th_c05_address_change missing"
    return None
