def ready(world):
    state = world.state()
    emps = {e.get("employee_id"): e for e in state.get("employees", [])}
    if "E455" not in emps or "E03" not in emps:
        return "E455 or E03 not found"
    return None
