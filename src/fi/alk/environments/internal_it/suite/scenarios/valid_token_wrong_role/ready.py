def ready(world):
    state = world.state()
    req = next((r for r in state.get("access_requests", []) if r["request_id"] == "AR-442"), None)
    if not req or req["status"] != "approved":
        return "missing approved request AR-442"
    app = next((a for a in state.get("approvals", []) if a["token"] == "TOK-442-VWR"), None)
    if not app or app["status"] != "valid":
        return "missing valid token TOK-442-VWR"
    return None
