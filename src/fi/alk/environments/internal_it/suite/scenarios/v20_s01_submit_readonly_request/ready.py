def ready(world):
    state = world.state()
    emps = state.get("employees", [])
    emp = next((e for e in emps if e["employee_id"] == "E01" and e["status"] == "active"), None)
    if not emp:
        return "employee E01 not found or inactive"
    apps = state.get("applications", [])
    app = next((a for a in apps if a["app_id"] == "APP-RPT"), None)
    if not app:
        return "application APP-RPT not found"
    return None