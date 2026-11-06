def ready(world):
    state = world.state()
    threads = state.get("threads", [])
    if not any(t["id"] == "th_c03_draft_needed" for t in threads):
        return "th_c03_draft_needed missing"
    return None