def ready(world):
    state = world.state()
    msg = next((m for m in state.get("slack_messages", []) if m.get("ts") == "1792504800.000014"), None)
    if not msg:
        return "Message SM14 missing from slack_messages"
    return None
