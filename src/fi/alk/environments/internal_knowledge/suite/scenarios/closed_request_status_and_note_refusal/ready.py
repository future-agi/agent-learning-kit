def ready(world):
    state = world.state()
    row = next((r for r in state.get("knowledge_requests", []) if r.get("request_id") == 'KR-103'), None)
    if not row or row.get("status") != 'closed':
        return "knowledge_requests KR-103 is missing or not closed"
    return None
