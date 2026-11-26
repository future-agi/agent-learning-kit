def ready(world):
    msg = next((m for m in world.state().get("slack_messages", []) if m["message_id"] == "IM405"), None)
    if not msg: return "IM405 missing"
    return None
