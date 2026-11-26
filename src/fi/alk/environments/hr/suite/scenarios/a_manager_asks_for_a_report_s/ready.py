def ready(world):
    state = world.state()
    emps = {e.get("employee_id"): e for e in state.get("employees", [])}
    if "M02" not in emps or "E06" not in emps:
        return "M02 or E06 not found"
    return None
