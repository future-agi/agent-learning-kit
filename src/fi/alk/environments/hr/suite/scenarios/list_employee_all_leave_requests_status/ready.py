def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E04"), None)
    if not emp:
        return "E04 must exist"
    return None