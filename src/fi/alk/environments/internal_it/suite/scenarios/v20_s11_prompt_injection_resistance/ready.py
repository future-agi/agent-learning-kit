def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E11" and e["status"] == "active"), None)
    if not emp:
        return "employee E11 not found or inactive"
    app = next((a for a in state.get("applications", []) if a["app_id"] == "APP-CRM"), None)
    if not app:
        return "application APP-CRM not found"
    return None