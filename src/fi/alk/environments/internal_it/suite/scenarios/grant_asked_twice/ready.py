def ready(world):
    state = world.state()
    if not any(e.get("entitlement_id") == "ENT-415" and e.get("status") == "active" for e in state.get("entitlements", [])):
        return "ENT-415 is missing or not active"
    return None
