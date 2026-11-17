def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E430"), None)
    if not emp:
        return "E430 missing"
    return None
