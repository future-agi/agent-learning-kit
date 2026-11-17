def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E436"), None)
    if not emp:
        return "employee E436 missing"
    req = next((r for r in state.get("access_requests", []) if r["request_id"] == "AR-436"), None)
    if not req:
        return "request AR-436 missing"
    return None
