def ready(world):
    state = world.state()
    reqs = [r for r in state.get("leave_requests", []) if r.get("request_id") == "REQ-1107"]
    if not reqs or reqs[0].get("status") != "rejected":
        return "REQ-1107 not rejected"
    return None
