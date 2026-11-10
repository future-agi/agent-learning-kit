def ready(world):
    state = world.state()
    e02 = next((e for e in state.get("employees", []) if e["employee_id"] == "E02"), None)
    if not e02:
        return "E02 not found in employees"
    return None
