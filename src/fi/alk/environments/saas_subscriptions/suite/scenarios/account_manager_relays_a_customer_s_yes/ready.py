def ready(world):
    msg = next((m for m in world.state().get("slack_messages", []) if m.get("channel_id") == "CH_BILLESC" and m.get("ts") == "1792000000.000405"), None)
    if not msg:
        return "Slack message 1792000000.000405 not found in CH_BILLESC"
    return None
