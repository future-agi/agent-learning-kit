def ready(world):
    approvals = world.state().get("slack_approvals", [])
    app = next((a for a in approvals if a["approval_id"] == "APP-SH-411"), None)
    if not app:
        return "APP-SH-411 missing"
    if app["status"] != "pending":
        return "APP-SH-411 is not pending"
    return None
