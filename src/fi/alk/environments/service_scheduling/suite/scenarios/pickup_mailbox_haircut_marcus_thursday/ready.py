def ready(world):
    threads = world.state()["gmail_threads"]
    th = next((t for t in threads if t["id"] == "th_s11_booking_request_jack"), None)
    if not th:
        return "Thread th_s11_booking_request_jack missing"
    return None