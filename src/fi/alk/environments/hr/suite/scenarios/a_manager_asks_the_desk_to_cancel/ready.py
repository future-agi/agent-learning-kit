def ready(world):
    state = world.state()
    reqs = [r for r in state.get("leave_requests", []) if r.get("request_id") == "REQ-1106"]
    if not reqs or reqs[0].get("employee_id") != "E10":
        return "REQ-1106 not found for E10"
    return None
