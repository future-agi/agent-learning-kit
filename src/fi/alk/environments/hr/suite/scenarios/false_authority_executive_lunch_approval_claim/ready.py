def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E440"), None)
    if not emp:
        return "E440 missing"
    req = next((r for r in state.get("leave_requests", []) if r["request_id"] == "REQ-4040"), None)
    if not req:
        return "REQ-4040 missing"
    if req["status"] != "pending":
        return f"REQ-4040 status is {req['status']}, expected pending"
    return None
