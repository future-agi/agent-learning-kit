def ready(world):
    ents = [e for e in world.state()["entitlements"] if e.get("entitlement_id") == "ENT-418-B"]
    if not ents:
        return "ENT-418-B not found"
    return None