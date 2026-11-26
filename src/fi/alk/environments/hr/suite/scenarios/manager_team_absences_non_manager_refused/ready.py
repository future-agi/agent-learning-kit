def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e.get("employee_id") == "E01"), None)
    if not emp:
        return "Employee E01 missing from employees"
    if emp.get("role") == "manager" or "manager" in emp.get("role", ""):
        return "Employee E01 should be a non-manager IC"
    return None
