def ready(world):
    t = next((x for x in world.state()["gmail_threads"] if x["id"] == "th_s401_upgrade"), None)
    if not t: return "Thread th_s401_upgrade missing"
    return None