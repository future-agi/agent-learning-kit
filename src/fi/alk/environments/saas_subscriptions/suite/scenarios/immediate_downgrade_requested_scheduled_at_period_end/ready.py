def ready(world):
    sub = next((s for s in world.state()["subscriptions"] if s["tenant_id"] == "T403" and s["status"] == "active"), None)
    if not sub: return "Subscription T403 missing"
    u = next((x for x in world.state()["usage_records"] if x["tenant_id"] == "T403"), None)
    if not u or u["current_value"] != 4: return "Usage not 4"
    return None