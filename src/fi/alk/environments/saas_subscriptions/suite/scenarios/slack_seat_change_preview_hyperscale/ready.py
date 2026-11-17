def ready(world):
    msg = next((m for m in world.state()["slack_messages"] if m.get("message_id") == "SM402"), None)
    if not msg:
        return "Slack message SM402 not found"
    return None
