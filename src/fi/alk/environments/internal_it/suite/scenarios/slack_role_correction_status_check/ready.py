def ready(world):
    req = next((r for r in world.state().get("access_requests", []) if r["request_id"] == "AR-103"), None)
    if not req or req["role_name"] != "viewer": return "AR-103 missing or not viewer"
    return None
