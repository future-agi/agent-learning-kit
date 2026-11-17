def ready(world):
    state = world.state()
    req = next((r for r in state.get("access_requests", []) if r["request_id"] == "AR-449"), None)
    if not req or req.get("status") != "cancelled":
        return "missing cancelled access request AR-449"
    app = next((a for a in state.get("approvals", []) if a["request_id"] == "AR-449"), None)
    if not app or app.get("status") != "void":
        return "missing void approval for AR-449"
    msg = next((m for m in state.get("slack_messages", []) if m["message_id"] == "IM441"), None)
    if not msg:
        return "missing slack message IM441"
    return None
