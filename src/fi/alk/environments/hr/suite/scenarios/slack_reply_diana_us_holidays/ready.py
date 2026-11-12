def ready(world):
    state = world.state()
    msg = next((m for m in state.get("slack_messages", []) if m.get("ts") == "1792854000.000008"), None)
    if not msg:
        return "Message SM08 missing from slack_messages"
    app = next((a for a in state.get("slack_approvals", []) if a.get("approval_id") == "APP-HR-412"), None)
    if not app:
        return "Approval APP-HR-412 missing from slack_approvals"
    return None
