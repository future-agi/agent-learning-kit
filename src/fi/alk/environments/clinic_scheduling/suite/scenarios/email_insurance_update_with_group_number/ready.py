def ready(world):
    state = world.state()
    th = next((r for r in state.get("gmail_threads", []) if r.get("id") == 'th_c404_insurance'), None)
    if not th:
        return "thread th_c404_insurance is missing"
    return None
