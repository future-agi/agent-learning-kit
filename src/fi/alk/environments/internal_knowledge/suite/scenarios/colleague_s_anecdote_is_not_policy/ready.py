def ready(world):
    m = next((x for x in world.state()["slack_messages"] if x["message_id"] == "KM416"), None)
    if not m:
        return "KM416 not found"
    return None