def ready(world):
    state = world.state()
    mgr = next((e for e in state.get("employees", []) if e.get("employee_id") == "M02"), None)
    if not mgr:
        return "Manager M02 missing from employees"
    return None
