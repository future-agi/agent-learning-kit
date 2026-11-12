def ready(world):
    m = next((x for x in world.state()["slack_messages"] if x["message_id"] == "KM412"), None)
    a = next((x for x in world.state()["slack_approvals"] if x["approval_id"] == "APP-KN-420"), None)
    return None if m and a else "setup missing"