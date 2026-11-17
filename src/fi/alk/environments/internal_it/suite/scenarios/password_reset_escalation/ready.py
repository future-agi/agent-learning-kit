def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E433"), None)
    if not emp:
        return "employee E433 missing"
    return None
