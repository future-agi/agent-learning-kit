def ready(world):
    ents = [e for e in world.state()["entitlements"] if e["entitlement_id"] == "ENT-412"]
    if not ents or ents[0]["status"] != "expired":
        return "ENT-412 is not expired"
    return None