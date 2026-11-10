def ready(world):
    msgs = {m["ts"]: m for m in world.state().get("slack_messages", [])}
    if "1791970200.000007" not in msgs:
        return "BM07 message missing from slack_messages"
    return None
