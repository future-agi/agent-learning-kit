def ready(world):
    msgs = {m["message_id"]: m for m in world.state().get("slack_messages", [])}
    if "SM06" not in msgs:
        return "SM06 missing from slack_messages"
    return None