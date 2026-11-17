def ready(world):
    state = world.state()
    msg = next((m for m in state.get("slack_messages", []) if m.get("ts") == "1792929600.000011"), None)
    if not msg:
        return "Message SM11 missing from slack_messages"
    app = next((a for a in state.get("slack_approvals", []) if a.get("approval_id") == "APP-HR-417"), None)
    if not app:
        return "Approval APP-HR-417 missing from slack_approvals"
    return None
