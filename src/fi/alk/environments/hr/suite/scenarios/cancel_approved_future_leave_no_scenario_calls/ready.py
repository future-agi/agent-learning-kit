def ready(world):
    state = world.state()
    reqs = [r for r in state.get("leave_requests", []) if r.get("request_id") == "REQ-4051"]
    if not reqs:
        return "REQ-4051 not found"
    if reqs[0].get("status") != "approved":
        return "REQ-4051 is not approved"
    return None
