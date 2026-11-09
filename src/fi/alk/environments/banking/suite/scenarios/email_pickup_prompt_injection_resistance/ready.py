def ready(world):
    state = world.state()
    threads = state.get("gmail_threads", [])
    if not any(t["id"] == "th_c05_prompt_injection" for t in threads):
        return "th_c05_prompt_injection missing"
    return None