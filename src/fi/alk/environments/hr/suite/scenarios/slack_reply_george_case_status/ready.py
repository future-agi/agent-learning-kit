def ready(world):
    state = world.state()
    msg = next((m for m in state.get("slack_messages", []) if m.get("ts") == "1792922400.000010"), None)
    if not msg:
        return "Message SM10 missing from slack_messages"
    case = next((c for c in state.get("hr_cases", []) if c.get("case_id") == "CASE-2901"), None)
    if not case:
        return "Case CASE-2901 missing from hr_cases"
    app = next((a for a in state.get("slack_approvals", []) if a.get("approval_id") == "APP-HR-414"), None)
    if not app:
        return "Approval APP-HR-414 missing from slack_approvals"
    return None
