def ready(world):
    state = world.state()
    req = next((r for r in state.get("leave_requests", []) if r.get("request_id") == "REQ-1101"), None)
    if not req:
        return "Request REQ-1101 missing from leave_requests"
    return None
