def ready(world):
    state = world.state()
    row = next((r for r in state.get("slack_messages", []) if r.get("message_id") == 'KM401'), None)
    if not row or row.get("user_id") != 'U_E20':
        return "slack_messages KM401 is missing or not posted by U_E20"
    return None
