def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E440"), None)
    if not emp:
        return "employee E440 missing"
    return None
