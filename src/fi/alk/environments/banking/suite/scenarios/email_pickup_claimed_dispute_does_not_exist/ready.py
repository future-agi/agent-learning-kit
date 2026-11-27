def ready(world):
    state = world.state()
    threads = {t["id"] for t in state.get("gmail_threads", [])}
    if "th_c04_claims_unverified" not in threads:
        return "th_c04_claims_unverified missing"
    return None
