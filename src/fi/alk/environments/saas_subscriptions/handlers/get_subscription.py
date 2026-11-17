def handle(args, db):
    tenant_id = args.get("tenant_id")
    actor_id = args.get("actor_id")
    if not tenant_id or not actor_id:
        raise ToolError("tenant_id and actor_id are required.")
    user = db.one("SELECT * FROM users WHERE tenant_id = ? AND actor_id = ?", [tenant_id, actor_id])
    if user is None:
        raise ToolError(f"User {actor_id!r} not found in tenant {tenant_id!r}.")
    sub = db.one("SELECT * FROM subscriptions WHERE tenant_id = ? AND status = 'active' ORDER BY current_period_end DESC", [tenant_id])
    if sub is None:
        raise ToolError(f"No active subscription found for tenant {tenant_id!r}.")
    plan = db.one("SELECT * FROM plans WHERE plan_id = ?", [sub["plan_id"]])
    pending = db.one("SELECT * FROM scheduled_changes WHERE tenant_id = ? AND status = 'pending' ORDER BY created_at DESC", [tenant_id])
    pending_dict = None
    if pending:
        pending_dict = {
            "change_id": pending["change_id"],
            "target_plan_id": pending["target_plan_id"],
            "target_seats": pending["target_seats"],
            "effective_at": pending["effective_at"],
            "status": pending["status"]
        }
    return {
        "subscription_id": sub["subscription_id"],
        "tenant_id": sub["tenant_id"],
        "plan_id": sub["plan_id"],
        "plan_name": plan["name"] if plan else sub["plan_id"],
        "status": sub["status"],
        "seat_count": sub["seat_count"],
        "current_period_start": sub["current_period_start"],
        "current_period_end": sub["current_period_end"],
        "cancel_at_period_end": bool(sub["cancel_at_period_end"]),
        "pending_change": pending_dict
    }