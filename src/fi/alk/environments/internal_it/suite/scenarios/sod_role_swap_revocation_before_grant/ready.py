def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E426"), None)
    if not emp:
        return "missing employee E426"
    ent = next((e for e in state.get("entitlements", []) if e["entitlement_id"] == "ENT-426" and e["status"] == "active"), None)
    if not ent:
        return "missing active entitlement ENT-426"
    return None
