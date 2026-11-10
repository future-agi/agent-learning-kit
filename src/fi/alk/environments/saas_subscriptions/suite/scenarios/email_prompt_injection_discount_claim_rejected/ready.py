def ready(world):
    t = next((x for x in world.state()["gmail_threads"] if x["id"] == "th_b05_injected"), None)
    if not t: return "Thread th_b05_injected missing"
    return None