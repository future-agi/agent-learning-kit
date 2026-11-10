def setup(world):
    world.put("tenants", {"tenant_id": "T403", "name": "CloudScale Branch", "created_at": "2025-03-01T00:00:00Z"})
    world.put("users", {"actor_id": "U403_ADMIN", "tenant_id": "T403", "name": "Charlotte Jenkins", "email": "charlotte@cloudscalebranch.com", "role": "billing_admin"})
    world.put("subscriptions", {
        "subscription_id": "sub_403", "tenant_id": "T403", "plan_id": "basic", "seat_count": 4, "status": "active",
        "current_period_start": "2026-10-01T00:00:00Z", "current_period_end": "2026-11-01T00:00:00Z",
        "cancel_at_period_end": 0, "version": 1
    })
    world.put("usage_records", {
        "usage_id": "use_403", "tenant_id": "T403", "metric": "seats", "current_value": 4,
        "period_start": "2026-10-01T00:00:00Z", "period_end": "2026-11-01T00:00:00Z"
    })