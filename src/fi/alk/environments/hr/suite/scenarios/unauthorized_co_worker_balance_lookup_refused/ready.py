def ready(world):
    state = world.state()
    e10 = next((e for e in state.get("employees", []) if e["employee_id"] == "E10"), None)
    if not e10:
        return "E10 missing in employees"
    return None
