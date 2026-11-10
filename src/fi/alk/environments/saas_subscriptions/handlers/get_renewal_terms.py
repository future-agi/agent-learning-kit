def handle(args, db):
    if not args.get('tenant_id') or not args.get('actor_id'):
        raise ToolError("tenant_id, actor_id are required.")
    tenant_id = args.get("tenant_id")
    actor_id = args.get("actor_id")
    user = db.one("SELECT * FROM users WHERE tenant_id = ? AND actor_id = ?", [tenant_id, actor_id])
    if user is None:
        raise ToolError(f"User {actor_id!r} is not a user of tenant {tenant_id!r}.")
    if user["role"] != "billing_admin":
        raise ToolError("Permission denied: only a billing_admin of the tenant may do this.")
    sub = db.one("SELECT * FROM subscriptions WHERE tenant_id = ? AND status = 'active'", [tenant_id])
    if sub is None:
        raise ToolError(f"No active subscription found for tenant {tenant_id!r}.")
    terms = db.one("SELECT * FROM renewal_terms WHERE subscription_id = ?", [sub["subscription_id"]])
    if terms is None:
        raise ToolError(f"No renewal terms recorded for {sub['subscription_id']}.")
    pending = db.one("SELECT * FROM cancellation_requests WHERE tenant_id = ? AND status = 'scheduled'", [tenant_id])
    return {"subscription_id": sub["subscription_id"], "plan_id": sub["plan_id"], "term": terms["term"], "term_end": terms["term_end"], "auto_renew": bool(terms["auto_renew"]), "notice_days": terms["notice_days"], "renewal_price_cents": terms["renewal_price_cents"], "current_period_end": sub["current_period_end"], "cancel_at_period_end": bool(sub["cancel_at_period_end"]), "scheduled_cancellation": pending["cancellation_id"] if pending else None}
