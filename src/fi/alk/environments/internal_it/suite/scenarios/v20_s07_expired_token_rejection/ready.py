def ready(world):
    state = world.state()
    req = next((r for r in state.get("access_requests", []) if r["request_id"] == "AR-07"), None)
    if not req:
        return "missing AR-07"
    appr = next((a for a in state.get("approvals", []) if a["token"] == "TOK-EXP-07"), None)
    if not appr:
        return "missing TOK-EXP-07"
    return None