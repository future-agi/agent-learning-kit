def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E10" and e["status"] == "active"), None)
    if not emp:
        return "employee E10 not found or inactive"
    return None