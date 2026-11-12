def ready(world):
    app = next((a for a in world.state().get("slack_approvals", []) if a.get("approval_id") == "APP-BL-401"), None)
    if not app or app.get("status") != "approved":
        return "APP-BL-401 approved record must exist"
    return None
