def setup(world):
    world.put("tenants", {"tenant_id": "T407", "name": "Tenant407", "created_at": "2025-10-01T00:00:00Z"})
    world.put("users", {"actor_id": "U407_ADMIN", "tenant_id": "T407", "name": "Rachel Radcliffe", "email": "rachel@tenant407.com", "role": "billing_admin"})
    world.put("subscriptions", {"subscription_id": "sub_407", "tenant_id": "T407", "plan_id": "pro", "status": "active", "seat_count": 8, "current_period_start": "2026-10-01T00:00:00Z", "current_period_end": "2026-11-01T00:00:00Z", "cancel_at_period_end": 0, "version": 1})
    world.put("renewal_terms", {"tenant_id": "T407", "subscription_id": "sub_407", "term": "monthly", "term_end": "2026-11-01T00:00:00Z", "auto_renew": 1, "notice_days": 0, "renewal_price_cents": 39900})
    world.put("scheduled_changes", {"change_id": "PC407", "tenant_id": "T407", "target_plan_id": "basic", "target_seats": 8, "effective_at": "2026-11-01T00:00:00Z", "status": "pending", "request_key": "req_pc407", "created_at": "2026-10-14T10:00:00Z"})
    world.put("billing_contacts", {"tenant_id": "T407", "name": "Rachel Radcliffe", "email": "rachel@tenant407.com", "updated_at": "2025-10-01T00:00:00Z", "updated_by": "U407_ADMIN"})
    world.change("session_caller", "default", {"actor_id": "U407_ADMIN"}, by="id")
