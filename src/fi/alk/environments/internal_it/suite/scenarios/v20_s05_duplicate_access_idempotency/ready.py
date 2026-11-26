def ready(world):
    state = world.state()
    ent = next((e for e in state.get("entitlements", []) if e["entitlement_id"] == "ENT-01" and e["status"] == "active"), None)
    if not ent:
        return "missing active entitlement ENT-01"
    return None