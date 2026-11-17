def ready(world):
    s = world.state()
    t = next((thr for thr in s.get("gmail_threads", []) if thr["id"] == "th_s405"), None)
    if not t:
        return "th_s405 thread missing"
    return None