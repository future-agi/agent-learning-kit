def ready(world):
    req = next((r for r in world.state().get("access_requests", []) if r["request_id"] == "AR-101"), None)
    if not req or req["approver_id"] != "E05": return "AR-101 missing or approver not E05"
    return None
