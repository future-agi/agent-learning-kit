def ready(world):
    state = world.state()
    msg = next((m for m in state.get("slack_messages", []) if m["message_id"] == "SM11"), None)
    if not msg:
        return "SM11 missing"
    pol = next((p for p in state.get("hr_policies", []) if p["region"] == "UK" and p["topic"] == "annual_leave" and p["version"] == "v2.0"), None)
    if not pol:
        return "UK annual leave policy missing"
    return None
