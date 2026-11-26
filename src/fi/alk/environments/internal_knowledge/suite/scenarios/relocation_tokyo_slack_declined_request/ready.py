def ready(world):
    state = world.state()
    row = next((r for r in state.get("employees", []) if r.get("employee_id") == 'E10'), None)
    if not row or row.get("status") != 'active':
        return "employees E10 is missing or its status is not active"
    return None
