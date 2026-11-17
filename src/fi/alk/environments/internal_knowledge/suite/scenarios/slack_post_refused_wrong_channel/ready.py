def ready(world):
    state = world.state()
    row = next((r for r in state.get("slack_approvals", []) if r.get("approval_id") == 'APP-KN-414'), None)
    if not row or row.get("status") != 'approved':
        return "slack_approvals APP-KN-414 is missing or its status is not approved"
    return None
