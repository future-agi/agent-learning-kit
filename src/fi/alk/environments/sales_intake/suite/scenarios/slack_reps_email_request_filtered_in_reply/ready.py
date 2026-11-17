def ready(world):
    state = world.state()
    if not any(m.get("message_id") == "SM401" for m in state.get("slack_messages", [])):
        return "message SM401 is missing"
    if not any(a.get("approval_id") == "APP-SH-408" for a in state.get("slack_approvals", [])):
        return "approval APP-SH-408 is missing"
    return None