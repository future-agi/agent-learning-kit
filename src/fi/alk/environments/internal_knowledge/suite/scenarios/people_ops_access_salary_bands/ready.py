def ready(world):
    state = world.state()
    row = next((r for r in state.get("employees", []) if r.get("employee_id") == 'E15'), None)
    if not row or row.get("role") != 'people_ops':
        return "employees E15 is missing or role is not people_ops"
    return None
