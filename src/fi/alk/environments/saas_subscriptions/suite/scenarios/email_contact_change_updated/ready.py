def ready(world):
    state = world.state()
    thread = next((t for t in state.get("gmail_threads", []) if t["id"] == "th_b09_contact_change"), None)
    if not thread:
        return "thread th_b09_contact_change missing"
    return None
