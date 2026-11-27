def setup(world):
    world.put("tenants", {"tenant_id": "T406", "name": "Tenant406", "created_at": "2025-10-01T00:00:00Z"})
    world.put("users", {"actor_id": "U406_ADMIN", "tenant_id": "T406", "name": "Daniel Drummond", "email": "daniel@tenant406.com", "role": "billing_admin"})
    world.put("subscriptions", {"subscription_id": "sub_406", "tenant_id": "T406", "plan_id": "pro", "status": "active", "seat_count": 10, "current_period_start": "2026-10-01T00:00:00Z", "current_period_end": "2026-11-01T00:00:00Z", "cancel_at_period_end": 0, "version": 1})
    world.put("scheduled_changes", {"change_id": "PC406", "tenant_id": "T406", "target_plan_id": "basic", "target_seats": 5, "effective_at": "2026-11-01T00:00:00Z", "status": "canceled", "request_key": "req_pc406", "created_at": "2026-10-14T10:00:00Z"})
    world.put("renewal_terms", {"tenant_id": "T406", "subscription_id": "sub_406", "term": "monthly", "term_end": "2026-11-01T00:00:00Z", "auto_renew": 1, "notice_days": 0, "renewal_price_cents": 44900})
    world.put("billing_contacts", {"tenant_id": "T406", "name": "Daniel Drummond", "email": "daniel@tenant406.com", "updated_at": "2025-10-01T00:00:00Z", "updated_by": "U406_ADMIN"})
    world.change("session_caller", "default", {"actor_id": "U406_ADMIN"}, by="id")
