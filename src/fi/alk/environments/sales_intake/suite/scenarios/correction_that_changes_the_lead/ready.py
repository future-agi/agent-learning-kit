def ready(world):
    msgs = world.state().get("slack_messages", [])
    if not any(m["message_id"] == "SM412" for m in msgs):
        return "SM412 missing"
    approvals = world.state().get("slack_approvals", [])
    if not any(a["approval_id"] == "APP-SH-412" for a in approvals):
        return "APP-SH-412 missing"
    return None
