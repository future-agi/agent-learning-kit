def ready(world):
    a = next((x for x in world.state()["slack_approvals"] if x["approval_id"] == "APP-KN-424"), None)
    if not a:
        return "APP-KN-424 missing"
    return None