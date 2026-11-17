def ready(world):
    state = world.state()
    reqs = state.get("access_requests", [])
    if not any(r["request_id"] == "AR-101" and r["status"] == "pending" for r in reqs):
        return "AR-101 is not pending in access_requests"
    msgs = state.get("slack_messages", [])
    if not any(m["ts"] == "1792663500.000001" and m["channel_id"] == "CH_ITACCESS" for m in msgs):
        return "IM01 message not found in slack_messages"
    return None