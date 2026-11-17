def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E448"), None)
    if not emp or emp["status"] != "active":
        return "missing active employee E448"
    ent = next((e for e in state.get("entitlements", []) if e["entitlement_id"] == "ENT-449" and e["status"] == "active"), None)
    if not ent:
        return "missing active entitlement ENT-449"
    return None
