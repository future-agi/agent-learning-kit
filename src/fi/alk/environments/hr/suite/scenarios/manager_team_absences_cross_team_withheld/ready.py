def ready(world):
    state = world.state()
    mgr = next((e for e in state.get("employees", []) if e.get("employee_id") == "M01"), None)
    if not mgr:
        return "Manager M01 missing from employees"
    return None
