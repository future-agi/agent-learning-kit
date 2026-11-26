def ready(world):
    msgs = world.state().get("slack_messages", [])
    if not any(m["message_id"] == "SM410" for m in msgs):
        return "SM410 missing"
    ledger = world.state().get("slack_message_ledger", [])
    if not any(entry["request_key"] == "REQ-SH-410" for entry in ledger):
        return "REQ-SH-410 missing from ledger"
    return None
