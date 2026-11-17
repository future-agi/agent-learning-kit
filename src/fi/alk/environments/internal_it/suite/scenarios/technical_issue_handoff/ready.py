def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E432"), None)
    if not emp:
        return "employee E432 missing"
    ent = next((e for e in state.get("entitlements", []) if e["entitlement_id"] == "ENT-432" and e["status"] == "active"), None)
    if not ent:
        return "entitlement ENT-432 missing"
    return None
