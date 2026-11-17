def ready(world):
    state = world.state()
    req103 = next((r for r in state.get("access_requests", []) if r["request_id"] == "AR-103"), None)
    if not req103 or req103["status"] != "pending":
        return "missing pending request AR-103"
    req106 = next((r for r in state.get("access_requests", []) if r["request_id"] == "AR-106"), None)
    if not req106 or req106["status"] != "pending":
        return "missing pending request AR-106"
    return None
