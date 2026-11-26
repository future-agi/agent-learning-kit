def ready(world):
    s = world.state()
    t = next((thr for thr in s.get("gmail_threads", []) if thr["id"] == "th_s406"), None)
    if not t:
        return "th_s406 thread missing"
    return None