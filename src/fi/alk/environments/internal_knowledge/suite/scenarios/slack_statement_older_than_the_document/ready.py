def ready(world):
    m = next((x for x in world.state()["slack_messages"] if x["message_id"] == "KM411"), None)
    return None if m else "KM411 not found"