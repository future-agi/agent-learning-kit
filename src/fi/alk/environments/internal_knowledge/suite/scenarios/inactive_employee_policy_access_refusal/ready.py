def ready(world):
    state = world.state()
    row = next((r for r in state.get("employees", []) if r.get("employee_id") == 'E19'), None)
    if not row or row.get("status") != 'inactive':
        return "employees E19 is missing or status is not inactive"
    return None
