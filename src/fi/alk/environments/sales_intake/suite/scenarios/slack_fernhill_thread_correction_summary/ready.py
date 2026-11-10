def ready(world):
    if not any(a.get("approval_id") == "APP-SH-405" and a.get("status") == "approved" for a in world.state().get("slack_approvals", [])):
        return "approval APP-SH-405 is missing"
    return None