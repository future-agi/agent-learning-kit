def ready(world):
    m = next((x for x in world.state()["slack_messages"] if x["message_id"] == "KM413"), None)
    if not m:
        return "KM413 not ready"
    return None