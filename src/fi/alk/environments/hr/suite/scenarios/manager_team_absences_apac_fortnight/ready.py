def ready(world):
    state = world.state()
    mgr = next((e for e in state.get("employees", []) if e.get("employee_id") == "M04"), None)
    if not mgr:
        return "Manager M04 missing from employees"
    return None
