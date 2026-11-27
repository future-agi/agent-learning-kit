def ready(world):
    state = world.state()
    msg = next((m for m in state.get("slack_messages", []) if m["ts"] == "1792918800.000008"), None)
    if not msg:
        return "slack message IM08 missing"
    return None
