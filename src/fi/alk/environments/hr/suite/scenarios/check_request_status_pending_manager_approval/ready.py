def ready(world):
    state = world.state()
    req = next((r for r in state.get("leave_requests", []) if r["request_id"] == "REQ-1003"), None)
    if not req:
        return "REQ-1003 missing in leave_requests"
    if req["status"] != "pending":
        return f"REQ-1003 expected status 'pending', got {req['status']!r}"
    return None
