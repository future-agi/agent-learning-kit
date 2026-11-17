def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E03" and e["status"] == "active"), None)
    if not emp:
        return "employee E03 not found or inactive"
    app = next((a for a in state.get("applications", []) if a["app_id"] == "APP-FIN"), None)
    if not app:
        return "application APP-FIN not found"
    return None