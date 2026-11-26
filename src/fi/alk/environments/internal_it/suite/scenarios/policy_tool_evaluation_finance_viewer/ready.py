def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E424"), None)
    if not emp:
        return "missing employee E424"
    return None
