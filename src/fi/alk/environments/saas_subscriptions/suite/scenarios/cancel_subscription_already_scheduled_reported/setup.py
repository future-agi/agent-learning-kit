def setup(world):
    world.put("tenants", {"tenant_id": "T401", "name": "Apex Innovations", "created_at": "2025-01-01T00:00:00Z"})
    world.put("users", {"actor_id": "U401_ADMIN", "tenant_id": "T401", "name": "Arthur Pendelton", "email": "arthur@apex.example", "role": "billing_admin"})
    world.put("subscriptions", {"subscription_id": "sub_401", "tenant_id": "T401", "plan_id": "pro", "seat_count": 5, "status": "active", "current_period_start": "2026-10-01T00:00:00Z", "current_period_end": "2026-11-01T00:00:00Z", "cancel_at_period_end": 1, "version": 1})
    world.put("renewal_terms", {"tenant_id": "T401", "subscription_id": "sub_401", "term": "monthly", "term_end": "2026-11-01T00:00:00Z", "auto_renew": 0, "notice_days": 0, "renewal_price_cents": 32400})
    world.put("billing_contacts", {"tenant_id": "T401", "name": "Arthur Pendelton", "email": "arthur@apex.example", "updated_at": "2025-12-01T00:00:00Z", "updated_by": "U401_ADMIN"})
    world.put("cancellation_requests", {"cancellation_id": "CAN-401", "tenant_id": "T401", "subscription_id": "sub_401", "effective_at": "2026-11-01T00:00:00Z", "status": "scheduled", "request_key": "seed-can-401", "requested_by": "U401_ADMIN", "created_at": "2026-10-10T12:00:00Z"})
    world.change("session_caller", "default", {"actor_id": "U401_ADMIN"}, by="id")
