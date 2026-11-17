def ready(world):
    state = world.state()
    emps = [e for e in state.get("employees", []) if e.get("employee_id") == "E459"]
    if not emps:
        return "E459 not found"
    return None
