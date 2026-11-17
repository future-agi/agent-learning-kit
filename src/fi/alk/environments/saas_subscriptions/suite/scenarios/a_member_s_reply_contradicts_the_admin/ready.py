def ready(world):
    th = next((t for t in world.state().get("gmail_threads", []) if t.get("id") == "th_s403_seat_request"), None)
    if not th:
        return "th_s403_seat_request thread not found"
    return None
