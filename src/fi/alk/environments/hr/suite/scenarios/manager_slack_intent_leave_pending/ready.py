def ready(world):
    state = world.state()
    req = next((r for r in state.get("leave_requests", []) if r["request_id"] == "REQ-1103"), None)
    if not req or req["status"] != "pending":
        return "REQ-1103 must exist and be pending"
    msg = next((m for m in state.get("slack_messages", []) if m["ts"] == "1792659600.000013"), None)
    if not msg:
        return "Slack message SM13 (1792659600.000013) missing"
    return None