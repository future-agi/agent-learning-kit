def ready(world):
    state = world.state()
    row = next((r for r in state.get("employees", []) if r.get("employee_id") == 'E06'), None)
    if not row or row.get("status") != 'active':
        return "employees E06 is missing or its status is not active"
    ch = next((c for c in state.get("slack_channels", []) if c.get("channel_id") == 'CH_LEADERSHIP'), None)
    if not ch or ch.get("is_private") != 1:
        return "slack_channel CH_LEADERSHIP is missing or not private"
    return None
