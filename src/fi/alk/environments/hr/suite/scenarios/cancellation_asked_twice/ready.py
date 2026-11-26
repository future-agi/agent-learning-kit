def ready(world):
    state = world.state()
    reqs = [r for r in state.get("leave_requests", []) if r.get("request_id") == "REQ-4052"]
    if not reqs:
        return "REQ-4052 not found"
    if reqs[0].get("status") != "pending":
        return "REQ-4052 is not pending"
    return None
