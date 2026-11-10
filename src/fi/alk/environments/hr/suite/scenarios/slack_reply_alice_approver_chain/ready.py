def ready(world):
    state = world.state()
    msg = next((m for m in state.get("slack_messages", []) if m.get("ts") == "1792773000.000012"), None)
    if not msg:
        return "Message SM12 missing from slack_messages"
    app = next((a for a in state.get("slack_approvals", []) if a.get("approval_id") == "APP-HR-413"), None)
    if not app:
        return "Approval APP-HR-413 missing from slack_approvals"
    return None
