def ready(world):
    state = world.state()
    reqs = {r.get("request_id"): r for r in state.get("leave_requests", [])}
    if "REQ-4053" not in reqs or "REQ-4054" not in reqs:
        return "Requests not found"
    if reqs["REQ-4053"].get("status") != "pending" or reqs["REQ-4054"].get("status") != "pending":
        return "Requests not pending"
    return None
