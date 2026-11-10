def ready(world):
    state = world.state()
    row = next((r for r in state.get("employees", []) if r.get("employee_id") == 'E05'), None)
    if not row or row.get("status") != 'active':
        return "employees E05 is missing or status is not active"
    return None
