def ready(world):
    ents = [e for e in world.state()["entitlements"] if e.get("entitlement_id") == "ENT-421"]
    if not ents:
        return "ENT-421 not found"
    return None