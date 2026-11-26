def ready(world):
    state = world.state()
    ent = next((e for e in state.get("entitlements", []) if e["entitlement_id"] == "ENT-FIN-06" and e["status"] == "active"), None)
    if not ent:
        return "missing active accountant entitlement ENT-FIN-06"
    return None