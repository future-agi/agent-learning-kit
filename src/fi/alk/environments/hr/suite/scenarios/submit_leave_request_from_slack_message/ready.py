def ready(world):
    emps = {e["employee_id"]: e for e in world.state().get("employees", [])}
    if "E413" not in emps:
        return "employee E413 is missing"
    msgs = {m["message_id"]: m for m in world.state().get("slack_messages", [])}
    if "SM413" not in msgs:
        return "slack message SM413 is missing"
    return None
