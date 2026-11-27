def ready(world):
    req = next((r for r in world.state().get("access_requests", []) if r["request_id"] == "AR-107" and r["status"] == "rejected"), None)
    if not req: return "AR-107 missing or not rejected"
    return None
