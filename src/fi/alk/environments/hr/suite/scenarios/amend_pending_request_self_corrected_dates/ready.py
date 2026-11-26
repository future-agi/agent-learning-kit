def ready(world):
    reqs = world.state()["leave_requests"]
    req = next((r for r in reqs if r["request_id"] == "REQ-4014"), None)
    if not req:
        return "REQ-4014 not found in leave_requests"
    if req["status"] != "pending":
        return f"REQ-4014 status is {req['status']}, expected pending"
    return None
