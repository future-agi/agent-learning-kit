def handle(args, db):
    if not args.get('tenant_id') or not args.get('actor_id') or not args.get('effective_timing') or not args.get('request_key'):
        raise ToolError("tenant_id, actor_id, effective_timing, request_key are required.")
    tenant_id = args.get("tenant_id")
    actor_id = args.get("actor_id")
    user = db.one("SELECT * FROM users WHERE tenant_id = ? AND actor_id = ?", [tenant_id, actor_id])
    if user is None:
        raise ToolError(f"User {actor_id!r} is not a user of tenant {tenant_id!r}.")
    if user["role"] != "billing_admin":
        raise ToolError("Permission denied: only a billing_admin of the tenant may do this.")
    if args["effective_timing"] == "immediate":
        raise ToolError("Immediate cancellation is not available: a subscription is cancelled at the end of its current term, and access continues until then.")
    if args["effective_timing"] != "end_of_term":
        raise ToolError("effective_timing must be 'end_of_term'.")
    if str(args.get("confirmation_code") or "").strip().lower() not in ("confirmed", "yes", "true", "approved", "confirm"):
        raise ToolError("Explicit confirmation from the billing admin is required first.")
    existing = db.one("SELECT * FROM cancellation_requests WHERE tenant_id = ? AND (request_key = ? OR status = 'scheduled')", [tenant_id, args["request_key"]])
    if existing:
        return {"cancellation_id": existing["cancellation_id"], "subscription_id": existing["subscription_id"], "effective_at": existing["effective_at"], "status": existing["status"], "idempotent": True, "message": "A cancellation is already scheduled; nothing new was created."}
    sub = db.one("SELECT * FROM subscriptions WHERE tenant_id = ? AND status = 'active'", [tenant_id])
    if sub is None:
        raise ToolError(f"No active subscription found for tenant {tenant_id!r}.")
    terms = db.one("SELECT * FROM renewal_terms WHERE subscription_id = ?", [sub["subscription_id"]])
    effective_at = terms["term_end"] if terms else sub["current_period_end"]
    now = db.one("SELECT now FROM clock")["now"]
    n = db.one("SELECT COUNT(*) AS c FROM cancellation_requests")["c"]
    cid = f"CX-{501 + n}"
    db.execute("INSERT INTO cancellation_requests (cancellation_id, tenant_id, subscription_id, effective_at, status, request_key, requested_by, created_at) VALUES (?, ?, ?, ?, 'scheduled', ?, ?, ?)", [cid, tenant_id, sub["subscription_id"], effective_at, args["request_key"], actor_id, now])
    db.execute("UPDATE subscriptions SET cancel_at_period_end = TRUE WHERE subscription_id = ?", [sub["subscription_id"]])
    return {"cancellation_id": cid, "subscription_id": sub["subscription_id"], "effective_at": effective_at, "status": "scheduled", "idempotent": False, "message": f"The subscription stays active, with its plan and seats, until {effective_at}, and then ends."}
