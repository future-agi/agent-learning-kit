def ready(world):
    msg = next((m for m in world.state().get("slack_messages", []) if m["ts"] == "1792755000.000003"), None)
    if not msg: return "Slack message 1792755000.000003 missing"
    return None
