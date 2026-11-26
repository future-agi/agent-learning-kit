def ready(world):
    state = world.state()
    reqs = [r for r in state.get("leave_requests", []) if r["employee_id"] == "E08"]
    if not reqs:
        return "E08 approved existing request missing"
    return None
