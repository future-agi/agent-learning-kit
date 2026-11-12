def ready(world):
    reqs = world.state()["leave_requests"]
    req = next((r for r in reqs if r["request_id"] == "REQ-1108"), None)
    if not req:
        return "REQ-1108 not found in leave_requests"
    if req["status"] != "approved":
        return f"REQ-1108 status is {req['status']}, expected approved"
    return None
