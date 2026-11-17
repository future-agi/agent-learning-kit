def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E428"), None)
    if not emp:
        return "missing employee E428"
    active_sec_ents = [e for e in state.get("entitlements", []) if e["app_id"] == "APP-SEC" and e["status"] == "active"]
    if len(active_sec_ents) < 4:
        return f"expected at least 4 active APP-SEC entitlements to exhaust seats, found {len(active_sec_ents)}"
    return None
