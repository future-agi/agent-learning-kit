def ready(world):
    state = world.state()
    apps = [a for a in state.get("slack_approvals", []) if a.get("approval_id") == "APP-HR-450"]
    if not apps or apps[0].get("status") != "revoked":
        return "APP-HR-450 not revoked"
    return None
