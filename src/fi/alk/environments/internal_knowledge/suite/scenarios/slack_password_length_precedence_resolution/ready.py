def ready(world):
    state = world.state()
    row = next((r for r in state.get("employees", []) if r.get("employee_id") == 'E09'), None)
    if not row or row.get("status") != 'active':
        return "employees E09 is missing or its status is not active"
    msg = next((m for m in state.get("slack_messages", []) if m.get("message_id") == 'KM15'), None)
    if not msg:
        return "slack message KM15 is missing"
    return None
