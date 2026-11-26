def ready(world):
    state = world.state()
    msg = next((m for m in state.get("slack_messages", []) if m["message_id"] == "SM414"), None)
    if not msg:
        return "SM414 missing"
    return None
