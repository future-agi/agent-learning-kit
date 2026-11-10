def ready(world):
    msg = next((m for m in world.state()["slack_messages"] if m.get("message_id") == "BM07"), None)
    if not msg: return "Slack message BM07 missing"
    return None