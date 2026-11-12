def ready(world):
    state = world.state()
    req = next((r for r in state.get("leave_requests", []) if r["request_id"] == "REQ-1109"), None)
    if not req or req["status"] != "pending":
        return "REQ-1109 must exist and be pending"
    msg = next((m for m in state.get("slack_messages", []) if m["ts"] == "1792773000.000012"), None)
    if not msg:
        return "Slack message SM12 (1792773000.000012) missing"
    return None