def ready(world):
    msg = next((m for m in world.state().get("slack_messages", []) if m["ts"] == "1792922400.000009"), None)
    if not msg: return "Slack message 1792922400.000009 missing"
    return None
