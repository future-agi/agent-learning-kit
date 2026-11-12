def ready(world):
    reqs = {r["request_id"]: r for r in world.state().get("leave_requests", [])}
    if "REQ-4103" not in reqs:
        return "REQ-4103 is missing"
    msgs = {m["message_id"]: m for m in world.state().get("slack_messages", [])}
    if "SM423" not in msgs or "SM424" not in msgs:
        return "SM423 or SM424 is missing"
    return None
