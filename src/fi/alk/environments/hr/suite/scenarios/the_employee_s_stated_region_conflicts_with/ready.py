def ready(world):
    state = world.state()
    emps = [e for e in state.get("employees", []) if e.get("employee_id") == "E454"]
    if not emps or emps[0].get("region") != "APAC":
        return "E454 not in APAC"
    return None
