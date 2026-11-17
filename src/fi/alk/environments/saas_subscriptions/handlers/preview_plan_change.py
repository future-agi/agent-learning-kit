def handle(args, db):
    import json
    tenant_id = args.get("tenant_id")
    target_plan_id = args.get("target_plan_id")
    target_seats = args.get("target_seats")
    effective_timing = args.get("effective_timing")
    actor_id = args.get("actor_id")
    if not tenant_id or not target_plan_id or target_seats is None or not effective_timing or not actor_id:
        raise ToolError("tenant_id, target_plan_id, target_seats, effective_timing, and actor_id are required.")
    if effective_timing not in ("end_of_period", "immediate"):
        raise ToolError(f"effective_timing must be 'end_of_period' or 'immediate', not {effective_timing!r}.")
    user = _entitled(db, tenant_id, actor_id, "member")
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
    
    timing_mode = db.one("SELECT value FROM environment_policy WHERE name = 'timing_mode'")
    if effective_timing == "immediate" and delta_monthly < 0 and (json.loads(timing_mode["value"]) if timing_mode else "end_of_period") == "end_of_period":
        warnings.append("A downgrade cannot take effect immediately; it would be scheduled for the end of the billing period.")
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


def _entitled(db, tenant_id, actor_id, need):
    """Who the desk acts for is decided by who is signed in to the conversation (session_caller),
    never by the actor_id the agent passes: actor_id must be that caller, and it grants only what the
    world records for them. `need` is "self" (the caller is a user of the tenant: their own role),
    "member" (any user of the tenant, or Cloudnest staff entitled to the tenant), "read" (the tenant's
    billing admin, or entitled staff: invoices, payments, credits, terms and the user list) or "admin"
    (the tenant's billing admin only: every change to the subscription or the billing contact).
    Staff are entitled by role (billing lead, billing support, billing operations, finance: every
    tenant) or as the account manager the tenant is assigned to; staff never change a subscription."""
    row = db.one("SELECT actor_id FROM session_caller WHERE id = 'default'")
    caller = row["actor_id"] if row else None
    if not caller or actor_id != caller:
        raise ToolError(f"Permission denied: {actor_id!r} is not the person signed in to this conversation ({caller or 'nobody'}); the desk acts only for the caller.")
    user = db.one("SELECT * FROM users WHERE tenant_id = ? AND actor_id = ?", [tenant_id, actor_id])
    if user is not None:
        if need in ("read", "admin") and user["role"] != "billing_admin":
            raise ToolError("Permission denied: only a billing_admin of the tenant may do this.")
        return user
    staff = db.one("SELECT * FROM slack_users WHERE user_id = ?", [actor_id])
    if staff is not None and need in ("member", "read"):
        desk = staff["role"] in ("billing_lead", "billing_support", "billing_operations", "finance")
        assigned = db.one("SELECT 1 FROM account_assignments WHERE tenant_id = ? AND user_id = ?", [tenant_id, actor_id])
        if desk or assigned:
            return {"actor_id": actor_id, "tenant_id": tenant_id, "name": staff["name"], "email": staff["email"], "role": staff["role"]}
    if staff is not None:
        raise ToolError(f"Permission denied: {actor_id!r} is Cloudnest staff, not a user of tenant {tenant_id!r}; only the tenant's billing admin may do this.")
    raise ToolError(f"User {actor_id!r} is not a user of tenant {tenant_id!r}.")
