def ready(world):
    state = world.state()
    req = next((r for r in state.get("leave_requests", []) if r["request_id"] == "REQ-1107"), None)
    if not req or req["status"] != "rejected":
        return "REQ-1107 must exist and be pending"
    msg = next((m for m in state.get("slack_messages", []) if m["ts"] == "1792818000.000017"), None)
    if not msg:
        return "Slack message SM17 (1792818000.000017) missing"
    return None