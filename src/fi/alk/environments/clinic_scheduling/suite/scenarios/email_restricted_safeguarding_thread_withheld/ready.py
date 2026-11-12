def ready(world):
    state = world.state()
    threads = {t["id"]: t for t in state.get("gmail_threads", [])}
    t = threads.get("th_c08_restricted_p18")
    if not t or not t.get("is_restricted"):
        return "thread th_c08_restricted_p18 is not marked restricted"
    return None
