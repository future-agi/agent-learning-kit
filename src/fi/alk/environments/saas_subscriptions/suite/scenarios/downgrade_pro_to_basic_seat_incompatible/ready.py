def ready(world):
    sub = next((s for s in world.state()["subscriptions"] if s["tenant_id"] == "T06" and s["status"] == "active"), None)
    if not sub:
        return "Tenant T06 does not have an active subscription"
    if sub["seat_count"] <= 10:
        return f"Tenant T06 has {sub['seat_count']} seats, expected > 10"
    return None
