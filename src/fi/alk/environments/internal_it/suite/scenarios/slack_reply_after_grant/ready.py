def ready(world):
    approvals = [a for a in world.state()["slack_approvals"] if a["approval_id"] == "APP-IT-403"]
    if not approvals:
        return "missing slack approval APP-IT-403"
    return None