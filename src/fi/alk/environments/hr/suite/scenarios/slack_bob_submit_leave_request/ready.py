def ready(world):
    state = world.state()
    msg = next((m for m in state.get("slack_messages", []) if m.get("ts") == "1792600000.000401"), None)
    if not msg:
        return "Message SM401 missing from slack_messages"
    return None
