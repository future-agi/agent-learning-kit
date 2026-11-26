def ready(world):
    state = world.state()
    emps = {e.get("employee_id"): e for e in state.get("employees", [])}
    if "E460" not in emps or "E05" not in emps:
        return "E460 or E05 not found"
    return None
