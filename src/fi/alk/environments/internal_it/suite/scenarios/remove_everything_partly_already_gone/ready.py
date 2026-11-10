def ready(world):
    state = world.state()
    e447 = next((e for e in state.get("entitlements", []) if e["entitlement_id"] == "ENT-447" and e["status"] == "active"), None)
    if not e447:
        return "missing active entitlement ENT-447"
    e448 = next((e for e in state.get("entitlements", []) if e["entitlement_id"] == "ENT-448" and e["status"] == "revoked"), None)
    if not e448:
        return "missing revoked entitlement ENT-448"
    return None
