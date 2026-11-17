def ready(world):
    state = world.state()
    reqs = state.get("access_requests", [])
    if not any(r["request_id"] == "AR-105" and r["status"] == "pending" for r in reqs):
        return "AR-105 not found as pending"
    msgs = state.get("slack_messages", [])
    if not any(m["ts"] == "1792830600.000006" for m in msgs):
        return "IM06 not found in slack_messages"
    return None