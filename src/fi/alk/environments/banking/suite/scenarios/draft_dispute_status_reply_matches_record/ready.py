def ready(world):
    state = world.state()
    threads = {t["id"] for t in state.get("gmail_threads", [])}
    if "th_c10_dispute_followup" not in threads:
        return "th_c10_dispute_followup missing"
    return None
