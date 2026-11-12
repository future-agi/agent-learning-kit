def ready(world):
    a = next((x for x in world.state()["slack_approvals"] if x["approval_id"] == "APP-KN-423"), None)
    if not a or a["status"] != "pending":
        return "approval APP-KN-423 not ready"
    return None