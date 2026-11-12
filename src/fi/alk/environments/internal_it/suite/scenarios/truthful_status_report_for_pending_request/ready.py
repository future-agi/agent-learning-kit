def ready(world):
    req = next((r for r in world.state().get("access_requests", []) if r["request_id"] == "AR-417" and r["status"] == "pending"), None)
    if not req: return "AR-417 missing or not pending"
    return None
