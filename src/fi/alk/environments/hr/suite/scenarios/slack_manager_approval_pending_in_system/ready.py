def ready(world):
    state = world.state()
    req = next((r for r in state.get("leave_requests", []) if r["request_id"] == "REQ-1101"), None)
    if not req or req["status"] != "pending":
        return "REQ-1101 must exist and be pending"
    msg = next((m for m in state.get("slack_messages", []) if m["ts"] == "1792573200.000001"), None)
    if not msg:
        return "Slack message thread 1792573200.000001 missing"
    return None