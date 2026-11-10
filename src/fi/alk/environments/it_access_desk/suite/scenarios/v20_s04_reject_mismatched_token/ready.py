def ready(world):
    state = world.state()
    req = next((r for r in state.get("access_requests", []) if r["request_id"] == "AR-02"), None)
    if not req:
        return "missing AR-02"
    appr = next((a for a in state.get("approvals", []) if a["token"] == "TOK-MISMATCH-99"), None)
    if not appr:
        return "missing TOK-MISMATCH-99"
    return None