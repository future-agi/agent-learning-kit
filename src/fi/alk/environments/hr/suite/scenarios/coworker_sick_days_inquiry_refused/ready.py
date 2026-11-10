def ready(world):
    state = world.state()
    emp1 = next((e for e in state.get("employees", []) if e["employee_id"] == "E11"), None)
    if not emp1:
        return "E11 missing"
    emp2 = next((e for e in state.get("employees", []) if e["employee_id"] == "E10"), None)
    if not emp2:
        return "E10 missing"
    return None
