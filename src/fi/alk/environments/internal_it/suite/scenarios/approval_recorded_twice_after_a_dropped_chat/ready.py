def ready(world):
    state = world.state()
    req = next((r for r in state.get("access_requests", []) if r.get("request_id") == "AR-452"), None)
    if not req or req.get("status") != "approved":
        return "AR-452 missing or not approved"
    appr = next((a for a in state.get("approvals", []) if a.get("approval_id") == "APR-452"), None)
    if not appr or appr.get("token") != "TOK-452-OK" or appr.get("status") != "valid":
        return "APR-452 missing or invalid"
    return None
