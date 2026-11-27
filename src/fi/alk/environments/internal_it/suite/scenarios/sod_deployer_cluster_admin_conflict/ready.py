def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E02"), None)
    if not emp:
        return "missing employee E02"
    ent = next((e for e in state.get("entitlements", []) if e["entitlement_id"] == "ENT-106" and e["status"] == "active"), None)
    if not ent:
        return "missing active entitlement ENT-106"
    return None
