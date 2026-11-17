def ready(world):
    state = world.state()
    thread = next((t for t in state.get("gmail_threads", []) if t["id"] == "th_b07_renewal_question"), None)
    if not thread:
        return "thread th_b07_renewal_question missing"
    return None
