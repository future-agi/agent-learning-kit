def ready(world):
    state = world.state()
    row = next((r for r in state.get("employees", []) if r.get("employee_id") == 'E17'), None)
    if not row or row.get("role") != 'manager':
        return "employees E17 is missing or role is not manager"
    msg = next((m for m in state.get("slack_messages", []) if m.get("message_id") == 'KM16'), None)
    if not msg:
        return "slack_message KM16 is missing"
    return None
