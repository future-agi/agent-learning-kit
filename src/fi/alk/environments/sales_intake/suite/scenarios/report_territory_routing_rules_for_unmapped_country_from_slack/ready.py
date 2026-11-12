def ready(world):
    msgs = {m["message_id"]: m for m in world.state().get("slack_messages", [])}
    if "SM08" not in msgs:
        return "SM08 missing from slack_messages"
    return None