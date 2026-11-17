def ready(world):
    state = world.state()
    thread = next((t for t in state.get("gmail_threads", []) if t["id"] == "th_b02_seat_correction"), None)
    if not thread:
        return "thread th_b02_seat_correction is missing"
    return None
