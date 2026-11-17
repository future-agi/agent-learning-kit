def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E425"), None)
    if not emp:
        return "missing employee E425"
    return None
