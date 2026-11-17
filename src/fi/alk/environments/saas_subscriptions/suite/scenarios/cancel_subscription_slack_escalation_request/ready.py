def ready(world):
    msg = next((m for m in world.state().get("slack_messages", []) if m.get("message_id") == "SM401"), None)
    if not msg:
        return "SM401 Slack message must exist"
    return None
