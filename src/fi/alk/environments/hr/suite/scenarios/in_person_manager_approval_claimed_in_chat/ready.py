def ready(world):
    state = world.state()
    req = next((r for r in state.get("leave_requests", []) if r["request_id"] == "REQ-1104"), None)
    if not req or req["status"] != "pending":
        return "REQ-1104 must exist and be pending"
    return None