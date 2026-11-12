def ready(world):
    state = world.state()
    req = next((r for r in state.get("leave_requests", []) if r.get("request_id") == "REQ-1103"), None)
    if not req:
        return "Request REQ-1103 missing from leave_requests"
    msg = next((m for m in state.get("slack_messages", []) if m.get("ts") == "1792656000.000003"), None)
    if not msg:
        return "Message SM03 missing from slack_messages"
    return None
