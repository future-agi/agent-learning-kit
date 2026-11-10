def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E420"), None)
    if not emp:
        return "E420 must exist in employees"
    return None