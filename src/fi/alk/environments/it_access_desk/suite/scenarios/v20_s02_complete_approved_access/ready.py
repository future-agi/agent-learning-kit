def ready(world):
    state = world.state()
    req = next((r for r in state.get("access_requests", []) if r["request_id"] == "AR-01"), None)
    if not req:
        return "missing access request AR-01"
    appr = next((a for a in state.get("approvals", []) if a["token"] == "TOK-APP-01"), None)
    if not appr:
        return "missing approval TOK-APP-01"
    return None