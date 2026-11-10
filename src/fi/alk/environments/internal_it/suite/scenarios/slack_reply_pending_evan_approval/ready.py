def ready(world):
    state = world.state()
    if not any(a["approval_id"] == "APP-IT-401" for a in state.get("slack_approvals", [])):
        return "APP-IT-401 not found in slack_approvals"
    return None