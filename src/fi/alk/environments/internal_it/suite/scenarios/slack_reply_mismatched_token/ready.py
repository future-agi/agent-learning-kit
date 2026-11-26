def ready(world):
    state = world.state()
    if not any(a["approval_id"] == "APP-IT-402" for a in state.get("slack_approvals", [])):
        return "APP-IT-402 not found in slack_approvals"
    return None