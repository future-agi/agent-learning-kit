def ready(world):
    th = next((t for t in world.state().get("gmail_threads", []) if t.get("id") == "th_s402_cancel_lookalike"), None)
    if not th:
        return "th_s402_cancel_lookalike thread not found"
    return None
