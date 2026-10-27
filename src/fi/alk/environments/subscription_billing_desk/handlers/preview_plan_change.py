def handle(args, db):
    import json
    tenant_id = args.get("tenant_id")
    target_plan_id = args.get("target_plan_id")
    target_seats = args.get("target_seats")
    effective_timing = args.get("effective_timing")
    actor_id = args.get("actor_id")
    if not tenant_id or not target_plan_id or target_seats is None or not effective_timing or not actor_id:
        raise ToolError("tenant_id, target_plan_id, target_seats, effective_timing, and actor_id are required.")
    user = db.one("SELECT * FROM users WHERE tenant_id = ? AND actor_id = ?", [tenant_id, actor_id])
    if user is None:
        raise ToolError(f"User {actor_id!r} not found in tenant {tenant_id!r}.")
    sub = db.one("SELECT * FROM subscriptions WHERE tenant_id = ? AND status = 'active'", [tenant_id])
    if sub is None:
        raise ToolError(f"No active subscription found for tenant {tenant_id!r}.")
    curr_plan = db.one("SELECT * FROM plans WHERE plan_id = ?", [sub["plan_id"]])
    new_plan = db.one("SELECT * FROM plans WHERE plan_id = ?", [target_plan_id])
    if new_plan is None:
        raise ToolError(f"Target plan {target_plan_id!r} does not exist.")
    usage = db.one("SELECT current_value FROM usage_records WHERE tenant_id = ? AND metric = 'seats'", [tenant_id])
    active_seats = usage["current_value"] if usage else sub["seat_count"]
    
    warnings = []
    seats_compatible = True
    if active_seats > new_plan["max_seats"]:
        seats_compatible = False
        warnings.append(f"Active seat usage ({active_seats}) exceeds target plan limit ({new_plan['max_seats']}). Seats must be reduced before switching.")
    if target_seats > new_plan["max_seats"]:
        seats_compatible = False
        warnings.append(f"Requested seats ({target_seats}) exceeds target plan max limit ({new_plan['max_seats']}).")
    
    curr_raw_feat = curr_plan["features"]
    new_raw_feat = new_plan["features"]
    curr_features = set(json.loads(curr_raw_feat) if isinstance(curr_raw_feat, str) else curr_raw_feat)
    new_features = set(json.loads(new_raw_feat) if isinstance(new_raw_feat, str) else new_raw_feat)
    features_lost = sorted(list(curr_features - new_features))
    features_gained = sorted(list(new_features - curr_features))
    
    curr_monthly = curr_plan["base_price_cents"] + (sub["seat_count"] * curr_plan["seat_price_cents"])
    new_monthly = new_plan["base_price_cents"] + (target_seats * new_plan["seat_price_cents"])
    delta_monthly = new_monthly - curr_monthly
    
    effective_at = sub["current_period_end"] if effective_timing == "end_of_period" else "immediate"
    
    return {
        "current_plan_id": curr_plan["plan_id"],
        "target_plan_id": new_plan["plan_id"],
        "target_seats": target_seats,
        "effective_at": effective_at,
        "current_monthly_cents": curr_monthly,
        "new_monthly_cents": new_monthly,
        "monthly_delta_cents": delta_monthly,
        "seats_compatible": seats_compatible,
        "max_seats_allowed": new_plan["max_seats"],
        "features_lost": features_lost,
        "features_gained": features_gained,
        "warnings": warnings
    }