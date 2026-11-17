def ready(world):
    if not any(p["provider_id"] == "D03" for p in world.state()["providers"]):
        return "Provider D03 missing"
    return None
