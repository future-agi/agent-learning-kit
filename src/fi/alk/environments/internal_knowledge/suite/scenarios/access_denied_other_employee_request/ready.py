def ready(world):
    state = world.state()
    row = next((r for r in state.get("knowledge_requests", []) if r.get("request_id") == 'KR-102'), None)
    if not row or row.get("requester_id") != 'E05':
        return "knowledge_requests KR-102 is missing or does not belong to E05"
    return None
