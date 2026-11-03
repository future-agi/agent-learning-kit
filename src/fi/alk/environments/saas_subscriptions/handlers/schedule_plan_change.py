def handle(args, db):
    tenant_id = args.get("tenant_id")
    target_plan_id = args.get("target_plan_id")
    target_seats = args.get("target_seats")
    effective_timing = args.get("effective_timing")
    confirmation_code = args.get("confirmation_code")
    request_key = args.get("request_key")
    actor_id = args.get("actor_id")
    if not tenant_id or not target_plan_id or target_seats is None or not effective_timing or not confirmation_code or not request_key or not actor_id:
        raise ToolError("tenant_id, target_plan_id, target_seats, effective_timing, confirmation_code, request_key, and actor_id are required.")
    
    user = db.one("SELECT * FROM users WHERE tenant_id = ? AND actor_id = ?", [tenant_id, actor_id])
    if user is None:
        raise ToolError(f"User {actor_id!r} not found in tenant {tenant_id!r}.")
    if user["role"] != "billing_admin":
        raise ToolError("Permission denied: only billing_admin can schedule plan changes.")
    
    if confirmation_code.strip().lower() not in ["confirmed", "yes", "true", "approved", "confirm"]:
        raise ToolError(f"Plan change rejected: invalid confirmation code {confirmation_code!r}. Explicit user confirmation is required.")
    
    existing = db.one("SELECT * FROM scheduled_changes WHERE tenant_id = ? AND request_key = ?", [tenant_id, request_key])
    if existing:
        return {
            "change_id": existing["change_id"],
            "tenant_id": existing["tenant_id"],
            "target_plan_id": existing["target_plan_id"],
            "target_seats": existing["target_seats"],
            "effective_at": existing["effective_at"],
            "status": existing["status"],
            "request_key": existing["request_key"],
            "message": "Existing scheduled change returned (idempotent)."
        }
    
    sub = db.one("SELECT * FROM subscriptions WHERE tenant_id = ? AND status = 'active'", [tenant_id])
    if sub is None:
        raise ToolError(f"No active subscription found for tenant {tenant_id!r}.")
    
    new_plan = db.one("SELECT * FROM plans WHERE plan_id = ?", [target_plan_id])
    if new_plan is None:
        raise ToolError(f"Target plan {target_plan_id!r} does not exist.")
    
    usage = db.one("SELECT current_value FROM usage_records WHERE tenant_id = ? AND metric = 'seats'", [tenant_id])
    active_seats = usage["current_value"] if usage else sub["seat_count"]
    if active_seats > new_plan["max_seats"]:
        raise ToolError(f"Cannot schedule change: active seat usage ({active_seats}) exceeds target plan max seats ({new_plan['max_seats']}). Active members must be reduced first.")
    
    effective_at = sub["current_period_end"] if effective_timing == "end_of_period" else "immediate"
    clock = db.one("SELECT now FROM clock LIMIT 1")
    now_ts = clock["now"] if clock else "2026-10-15T12:00:00Z"
    
    count_row = db.one("SELECT COUNT(*) as c FROM scheduled_changes", [])
    count = count_row["c"] if count_row else 0
    change_id = f"PC{1001 + count}"
    
    db.execute("INSERT INTO scheduled_changes (change_id, tenant_id, target_plan_id, target_seats, effective_at, status, request_key, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
               [change_id, tenant_id, target_plan_id, target_seats, effective_at, request_key, now_ts])
    
    return {
        "change_id": change_id,
        "tenant_id": tenant_id,
        "target_plan_id": target_plan_id,
        "target_seats": target_seats,
        "effective_at": effective_at,
        "status": "pending",
        "request_key": request_key,
        "message": f"Successfully scheduled plan change to {new_plan['name']} effective at {effective_at}."
    }