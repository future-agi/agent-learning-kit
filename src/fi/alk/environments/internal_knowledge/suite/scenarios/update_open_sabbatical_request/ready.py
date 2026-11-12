def ready(world):
    state = world.state()
    row = next((r for r in state.get("knowledge_requests", []) if r.get("request_id") == 'KR-101'), None)
    if not row or row.get("status") != 'open':
        return "knowledge_requests KR-101 is missing or not open"
    return None
