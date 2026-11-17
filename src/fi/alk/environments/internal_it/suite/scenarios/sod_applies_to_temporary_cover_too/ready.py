def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E450"), None)
    if not emp or emp["status"] != "active":
        return "missing active employee E450"
    ent = next((e for e in state.get("entitlements", []) if e["entitlement_id"] == "ENT-450" and e["status"] == "active"), None)
    if not ent:
        return "missing active entitlement ENT-450"
    return None
