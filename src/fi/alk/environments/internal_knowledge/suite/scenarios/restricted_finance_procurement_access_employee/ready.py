def ready(world):
    state = world.state()
    row = next((r for r in state.get("employees", []) if r.get("employee_id") == 'E09'), None)
    if not row or row.get("role") != 'employee':
        return "employees E09 is missing or role is not employee"
    return None
