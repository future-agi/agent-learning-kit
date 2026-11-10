def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E451"), None)
    if not emp or emp["status"] != "inactive":
        return "missing inactive employee E451"
    req = next((r for r in state.get("access_requests", []) if r["request_id"] == "AR-451"), None)
    if not req or req["status"] != "approved":
        return "missing approved request AR-451"
    return None
