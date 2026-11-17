def ready(world):
    state = world.state()
    case = next((c for c in state.get("hr_cases", []) if c["case_id"] == "CASE-2901"), None)
    if not case:
        return "CASE-2901 missing"
    msg = next((m for m in state.get("slack_messages", []) if m["message_id"] == "SM10"), None)
    if not msg:
        return "SM10 missing"
    return None
