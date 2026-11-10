def setup(world):
    world.put("tenants", {"tenant_id": "T404", "name": "DataFlow Systems", "created_at": "2025-01-01T00:00:00Z"})
    world.put("users", {"actor_id": "U404_ADMIN", "tenant_id": "T404", "name": "Derek Coleman", "email": "derek@dataflowsys.example", "role": "billing_admin"})
    world.put("subscriptions", {"subscription_id": "sub_404", "tenant_id": "T404", "plan_id": "pro", "seat_count": 10, "status": "active", "current_period_start": "2026-03-20T00:00:00Z", "current_period_end": "2027-03-20T00:00:00Z", "cancel_at_period_end": 0, "version": 1})
    world.put("renewal_terms", {"tenant_id": "T404", "subscription_id": "sub_404", "term": "annual", "term_end": "2027-03-20T00:00:00Z", "auto_renew": 1, "notice_days": 60, "renewal_price_cents": 369900})
    world.put("billing_contacts", {"tenant_id": "T404", "name": "Derek Coleman", "email": "derek@dataflowsys.example", "updated_at": "2025-12-01T00:00:00Z", "updated_by": "U404_ADMIN"})
