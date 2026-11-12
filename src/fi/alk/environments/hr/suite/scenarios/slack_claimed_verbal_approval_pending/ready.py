def ready(world):
    state = world.state()
    req = next((r for r in state.get("leave_requests", []) if r["request_id"] == "REQ-1106"), None)
    if not req or req["status"] != "pending":
        return "REQ-1106 must exist and be pending"
    msg = next((m for m in state.get("slack_messages", []) if m["ts"] == "1792724400.000006"), None)
    if not msg:
        return "Slack message SM06 (1792724400.000006) missing"
    return None