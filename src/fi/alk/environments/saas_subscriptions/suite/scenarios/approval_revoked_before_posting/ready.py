def ready(world):
    app = next((a for a in world.state().get("slack_approvals", []) if a.get("approval_id") == "APP-BL-407"), None)
    if not app or app.get("status") != "revoked":
        return "APP-BL-407 revoked approval record not found"
    msg = next((m for m in world.state().get("slack_messages", []) if m.get("channel_id") == "CH_BILLESC" and m.get("ts") == "1791973800.000009"), None)
    if not msg:
        return "BM09 message 1791973800.000009 not found in CH_BILLESC"
    return None
