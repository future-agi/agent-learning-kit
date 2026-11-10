def ready(world):
    state = world.state()
    row = next((r for r in state.get("employees", []) if r.get("employee_id") == 'E14'), None)
    if not row or row.get("status") != 'active':
        return "employees E14 is missing or its status is not active"
    msg = next((m for m in state.get("slack_messages", []) if m.get("message_id") == 'KM18'), None)
    if not msg:
        return "slack message KM18 is missing"
    return None
