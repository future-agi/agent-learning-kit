def ready(world):
    req = [r for r in world.state()["access_requests"] if r.get("request_id") == "AR-422"]
    if not req:
        return "AR-422 not found"
    return None