def ready(world):
    msgs = world.state()["slack_messages"]
    m = next((x for x in msgs if x["ts"] == "1792680000.000010"), None)
    if not m:
        return "Thread parent message missing in slack_messages"
    return None
