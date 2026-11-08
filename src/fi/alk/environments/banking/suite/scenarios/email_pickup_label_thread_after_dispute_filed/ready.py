def ready(world):
    state = world.state()
    threads = {t["id"] for t in state.get("gmail_threads", [])}
    if "th_c03_duplicate_charge" not in threads:
        return "th_c03_duplicate_charge missing"
    return None
