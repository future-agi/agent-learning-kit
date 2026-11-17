def ready(world):
    threads = world.state().get("gmail_threads", [])
    if not any(t["id"] == "th_r02_damaged_sweatshirt" for t in threads):
        return "thread th_r02_damaged_sweatshirt not found"
    return None
