def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E436"), None)
    if not emp:
        return "E436 missing"
    return None
