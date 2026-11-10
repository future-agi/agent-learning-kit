def ready(world):
    threads = world.state().get("gmail_threads", [])
    if not any(t["id"] == "th_r16_damaged_bottle" for t in threads):
        return "thread th_r16_damaged_bottle not found"
    return None
