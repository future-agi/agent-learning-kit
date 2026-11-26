def ready(world):
    m = next((x for x in world.state()["slack_messages"] if x["message_id"] == "KM410"), None)
    return None if m else "KM410 not found"