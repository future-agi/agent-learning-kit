def ready(world):
    r = next((x for x in world.state()["knowledge_requests"] if x["request_id"] == "KR-410"), None)
    if not r or r["status"] != "open":
        return "KR-410 not ready"
    return None