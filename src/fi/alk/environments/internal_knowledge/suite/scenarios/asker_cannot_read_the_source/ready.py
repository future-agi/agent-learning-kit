def ready(world):
    m = next((x for x in world.state()["slack_messages"] if x["message_id"] == "KM415"), None)
    a = next((x for x in world.state()["slack_approvals"] if x["approval_id"] == "APP-KN-426"), None)
    if not m or not a:
        return "setup records missing"
    return None