def ready(world):
    state = world.state()
    th = next((r for r in state.get("gmail_threads", []) if r.get("id") == 'th_c08_restricted_p18'), None)
    if not th or th.get("is_restricted") != 1:
        return "restricted thread th_c08_restricted_p18 is missing or not restricted"
    return None
