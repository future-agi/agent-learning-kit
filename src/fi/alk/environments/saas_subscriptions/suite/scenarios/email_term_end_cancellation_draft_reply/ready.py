def ready(world):
    state = world.state()
    thread = next((t for t in state.get("gmail_threads", []) if t["id"] == "th_b03_cancel_term_end"), None)
    if not thread:
        return "thread th_b03_cancel_term_end missing"
    return None
