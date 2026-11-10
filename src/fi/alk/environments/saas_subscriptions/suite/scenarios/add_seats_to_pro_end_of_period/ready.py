def ready(world):
    sub = next((s for s in world.state()["subscriptions"] if s["tenant_id"] == "T10" and s["status"] == "active"), None)
    if not sub:
        return "Tenant T10 does not have an active subscription"
    if sub["seat_count"] != 15:
        return f"Tenant T10 has {sub['seat_count']} seats, expected 15"
    return None
