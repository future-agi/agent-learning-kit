def ready(world):
    state = world.state()
    msg = next((m for m in state.get("slack_messages", []) if m["message_id"] == "SM08"), None)
    if not msg:
        return "SM08 missing in slack_messages"
    return None
