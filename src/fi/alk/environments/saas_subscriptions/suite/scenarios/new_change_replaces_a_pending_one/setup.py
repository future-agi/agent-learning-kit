def setup(world):
    world.put("tenants", {"tenant_id": "T405", "name": "Tenant405", "created_at": "2025-10-01T00:00:00Z"})
    world.put("users", {"actor_id": "U405_ADMIN", "tenant_id": "T405", "name": "Nancy Naughton", "email": "nancy@tenant405.com", "role": "billing_admin"})
    world.put("subscriptions", {"subscription_id": "sub_405", "tenant_id": "T405", "plan_id": "pro", "status": "active", "seat_count": 20, "current_period_start": "2026-10-01T00:00:00Z", "current_period_end": "2026-11-01T00:00:00Z", "cancel_at_period_end": 0, "version": 1})
    world.put("scheduled_changes", {"change_id": "PC405", "tenant_id": "T405", "target_plan_id": "pro", "target_seats": 10, "effective_at": "2026-11-01T00:00:00Z", "status": "pending", "request_key": "req_pc405", "created_at": "2026-10-14T10:00:00Z"})
    world.put("renewal_terms", {"tenant_id": "T405", "subscription_id": "sub_405", "term": "monthly", "term_end": "2026-11-01T00:00:00Z", "auto_renew": 1, "notice_days": 0, "renewal_price_cents": 69900})
    world.put("billing_contacts", {"tenant_id": "T405", "name": "Nancy Naughton", "email": "nancy@tenant405.com", "updated_at": "2025-10-01T00:00:00Z", "updated_by": "U405_ADMIN"})
    world.change("session_caller", "default", {"actor_id": "U405_ADMIN"}, by="id")
