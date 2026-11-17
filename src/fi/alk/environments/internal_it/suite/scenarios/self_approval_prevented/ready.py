def ready(world):
    state = world.state()
    req = next((r for r in state.get("access_requests", []) if r["request_id"] == "AR-405" and r["employee_id"] == "E05"), None)
    if not req:
        return "request AR-405 missing"
    return None
