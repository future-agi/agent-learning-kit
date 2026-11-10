def ready(world):
    state = world.state()
    thread = next((t for t in state.get("gmail_threads", []) if t["id"] == "th_b05_injected"), None)
    if not thread:
        return "thread th_b05_injected is missing"
    return None
