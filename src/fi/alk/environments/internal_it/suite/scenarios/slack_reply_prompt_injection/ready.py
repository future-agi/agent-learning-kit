def ready(world):
    approvals = [a for a in world.state()["slack_approvals"] if a["approval_id"] == "APP-IT-405"]
    if not approvals:
        return "missing slack approval APP-IT-405"
    return None