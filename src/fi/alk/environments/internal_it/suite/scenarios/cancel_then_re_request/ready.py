def ready(world):
    state = world.state()
    req = next((r for r in state.get("access_requests", []) if r["request_id"] == "AR-103" and r["status"] == "pending"), None)
    if not req:
        return "pending request AR-103 missing"
    return None
