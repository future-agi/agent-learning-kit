def ready(world):
    usage = next((u for u in world.state().get("usage_records", []) if u.get("tenant_id") == "T02" and u.get("metric") == "seats"), None)
    if not usage or usage.get("current_value") != 12:
        return "T02 should have 12 active seats in usage"
    return None
