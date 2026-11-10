def ready(world):
    state = world.state()
    emps = [e for e in state.get("employees", []) if e.get("employee_id") == "E456"]
    if not emps:
        return "E456 not found"
    return None
