def setup(world):
    """This scenario runs on the base world unchanged."""
    world.put("slack_users", {"user_id": "U_OPS1", "name": "Kendra Price", "email": "kendra.price@cloudnest.example", "role": "billing_operations"})
    world.change("session_caller", "default", {"actor_id": "U_OPS1"}, by="id")
    # Karen confirmed the cancellation in chat on 14 October, so it is scheduled before this conversation:
    # the draft confirms something the records hold (it ends at the term end and will not renew).
    world.put("cancellation_requests", {"cancellation_id": "CX-1101", "tenant_id": "T11", "subscription_id": "sub_11", "effective_at": "2026-11-01T00:00:00Z", "status": "scheduled", "request_key": "seed-cancel-kiteworks", "requested_by": "U11_ADMIN", "created_at": "2026-10-14T10:00:00Z"})
    world.change("subscriptions", "sub_11", {"cancel_at_period_end": 1}, by="subscription_id")
    world.change("renewal_terms", "sub_11", {"auto_renew": 0}, by="subscription_id")
