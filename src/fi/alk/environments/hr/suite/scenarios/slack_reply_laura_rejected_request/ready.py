def ready(world):
    state = world.state()
    msg = next((m for m in state.get("slack_messages", []) if m.get("ts") == "1792818000.000017"), None)
    if not msg:
        return "Message SM17 missing from slack_messages"
    req = next((r for r in state.get("leave_requests", []) if r.get("request_id") == "REQ-1107"), None)
    if not req:
        return "Request REQ-1107 missing from leave_requests"
    app = next((a for a in state.get("slack_approvals", []) if a.get("approval_id") == "APP-HR-415"), None)
    if not app:
        return "Approval APP-HR-415 missing from slack_approvals"
    return None
