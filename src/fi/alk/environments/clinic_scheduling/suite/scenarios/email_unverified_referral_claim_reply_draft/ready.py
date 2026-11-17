def ready(world):
    state = world.state()
    threads = {t["id"]: t for t in state.get("gmail_threads", [])}
    if "th_c06_claim_p16" not in threads:
        return "thread th_c06_claim_p16 is missing"
    return None
