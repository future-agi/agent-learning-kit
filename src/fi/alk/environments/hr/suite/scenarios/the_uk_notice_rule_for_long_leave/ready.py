def ready(world):
    state = world.state()
    emps = [e for e in state.get("employees", []) if e.get("employee_id") == "E457"]
    if not emps or emps[0].get("region") != "UK":
        return "E457 not in UK"
    return None
