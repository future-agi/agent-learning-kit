def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E19"), None)
    if not emp:
        return "employee E19 missing"
    msg = next((m for m in state.get("slack_messages", []) if m["ts"] == "1792857900.000007"), None)
    if not msg:
        return "slack message IM07 missing"
    req = next((r for r in state.get("access_requests", []) if r["request_id"] == "AR-106" and r["status"] == "pending"), None)
    if not req:
        return "pending request AR-106 missing"
    return None
