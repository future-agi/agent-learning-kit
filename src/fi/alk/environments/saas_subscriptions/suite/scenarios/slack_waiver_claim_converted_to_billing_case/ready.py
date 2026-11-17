def ready(world):
    msg = next((m for m in world.state()["slack_messages"] if m.get("message_id") == "BM01"), None)
    if not msg: return "Slack parent message BM01 missing"
    return None