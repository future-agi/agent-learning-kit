def ready(world):
    state = world.state()
    row = next((r for r in state.get("gmail_threads", []) if r.get("id") == 'th_c01_tuesday_thursday'), None)
    if not row:
        return "gmail_threads th_c01_tuesday_thursday is missing"
    return None
