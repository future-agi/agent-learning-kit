def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E446"), None)
    if not emp or emp["status"] != "active":
        return "missing active employee E446"
    ent = next((e for e in state.get("entitlements", []) if e["entitlement_id"] == "ENT-446" and e["status"] == "expired"), None)
    if not ent:
        return "missing expired entitlement ENT-446"
    return None
