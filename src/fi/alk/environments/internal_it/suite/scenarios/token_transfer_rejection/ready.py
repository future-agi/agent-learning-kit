def ready(world):
    state = world.state()
    e1 = next((e for e in state.get("employees", []) if e["employee_id"] == "E437"), None)
    e2 = next((e for e in state.get("employees", []) if e["employee_id"] == "E438"), None)
    if not (e1 and e2):
        return "employees E437/E438 missing"
    req = next((r for r in state.get("access_requests", []) if r["request_id"] == "AR-438"), None)
    if not req:
        return "request AR-438 missing"
    return None
