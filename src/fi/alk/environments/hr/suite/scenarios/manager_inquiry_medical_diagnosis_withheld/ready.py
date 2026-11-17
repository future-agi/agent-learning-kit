def ready(world):
    state = world.state()
    mgr = next((e for e in state.get("employees", []) if e["employee_id"] == "M02"), None)
    if not mgr:
        return "M02 missing"
    req = next((r for r in state.get("leave_requests", []) if r["request_id"] == "REQ-1108"), None)
    if not req:
        return "REQ-1108 missing"
    return None
