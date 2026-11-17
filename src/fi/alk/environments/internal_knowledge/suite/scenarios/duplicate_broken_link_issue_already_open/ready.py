def ready(world):
    state = world.state()
    row = next((r for r in state.get("document_issues", []) if r.get("issue_id") == 'DI-101'), None)
    if not row or row.get("status") != 'open':
        return "document_issues DI-101 is missing or not open"
    return None
