def ready(world):
    r = next((x for x in world.state()["knowledge_requests"] if x["request_id"] == "KR-101"), None)
    return None if r else "KR-101 missing"