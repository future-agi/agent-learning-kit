def ready(world):
    state = world.state()
    msg = next((m for m in state.get("slack_messages", []) if m.get("ts") == "1792753200.000005"), None)
    if not msg:
        return "Message SM05 missing from slack_messages"
    return None
