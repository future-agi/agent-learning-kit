def ready(world):
    state = world.state()
    row = next((r for r in state.get("escalation_routes", []) if r.get("team") == 'nurse_line'), None)
    if not row:
        return "escalation_routes nurse_line is missing"
    return None
