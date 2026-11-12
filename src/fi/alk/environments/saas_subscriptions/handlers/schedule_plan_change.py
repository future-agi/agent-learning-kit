def _handle(args, db):
    import json
    tenant_id = args.get("tenant_id")
    target_plan_id = args.get("target_plan_id")
    target_seats = args.get("target_seats")
    effective_timing = args.get("effective_timing")
    confirmation_code = args.get("confirmation_code")
    request_key = args.get("request_key")
    actor_id = args.get("actor_id")
    if not tenant_id or not target_plan_id or target_seats is None or not effective_timing or not request_key or not actor_id:
        raise ToolError("tenant_id, target_plan_id, target_seats, effective_timing, request_key, and actor_id are required.")
    # A typo must not turn into the other timing: only these two are understood.
    if effective_timing not in ("end_of_period", "immediate"):
        raise ToolError(f"effective_timing must be 'end_of_period' or 'immediate', not {effective_timing!r}.")
    if isinstance(target_seats, bool) or not isinstance(target_seats, int) or target_seats < 1:
        raise ToolError("target_seats must be a whole number of at least 1.")

    user = _entitled(db, tenant_id, actor_id, "admin")
    policy = {r["name"]: json.loads(r["value"]) for r in db.query("SELECT name, value FROM environment_policy")}

    if policy.get("require_confirmation", True) and str(confirmation_code or "").strip().lower() not in ["confirmed", "yes", "true", "approved", "confirm"]:
        raise ToolError(f"Plan change rejected: invalid confirmation code {confirmation_code!r}. Explicit user confirmation is required.")

    sub = db.one("SELECT * FROM subscriptions WHERE tenant_id = ? AND status = 'active'", [tenant_id])
    if sub is None:
        raise ToolError(f"No active subscription found for tenant {tenant_id!r}.")

    # A replay returns the change already made only when it is the same request from the same
    # admin; a reused key for anything else is a conflict, not a success.
    existing = db.one("SELECT * FROM scheduled_changes WHERE tenant_id = ? AND request_key = ?", [tenant_id, request_key])
    if existing:
        same = (
            existing["target_plan_id"] == target_plan_id
            and existing["target_seats"] == target_seats
            and (existing["effective_timing"] or ("immediate" if existing["effective_at"] == "immediate" else "end_of_period")) == effective_timing
            and existing["requested_by"] in (None, actor_id)
        )
        if not same:
            raise ToolError(
                f"request_key {request_key!r} was already used for change {existing['change_id']} ({existing['target_plan_id']}, {existing['target_seats']} seats); a different request needs a new key.",
                category="conflict",
            )
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

    pending = db.one("SELECT change_id FROM scheduled_changes WHERE tenant_id = ? AND status = 'pending'", [tenant_id])
    if pending:
        raise ToolError(f"Change {pending['change_id']} is already pending for {tenant_id}; cancel it first, then schedule the new one.", category="conflict")

    new_plan = db.one("SELECT * FROM plans WHERE plan_id = ?", [target_plan_id])
    if new_plan is None:
        raise ToolError(f"Target plan {target_plan_id!r} does not exist.")
    cur_plan = db.one("SELECT * FROM plans WHERE plan_id = ?", [sub["plan_id"]])

    usage = db.one("SELECT current_value FROM usage_records WHERE tenant_id = ? AND metric = 'seats'", [tenant_id])
    active_seats = usage["current_value"] if usage else sub["seat_count"]
    if active_seats > new_plan["max_seats"]:
        raise ToolError(f"Cannot schedule change: active seat usage ({active_seats}) exceeds target plan max seats ({new_plan['max_seats']}). Active members must be reduced first.")
    if target_seats > new_plan["max_seats"]:
        raise ToolError(f"Cannot schedule change: {target_seats} seats exceeds the {new_plan['name']} plan's limit of {new_plan['max_seats']}.")

    cur_monthly = cur_plan["base_price_cents"] + sub["seat_count"] * cur_plan["seat_price_cents"]
    new_monthly = new_plan["base_price_cents"] + target_seats * new_plan["seat_price_cents"]
    if effective_timing == "immediate" and new_monthly < cur_monthly and policy.get("timing_mode", "end_of_period") == "end_of_period":
        raise ToolError("A downgrade takes effect at the end of the billing period, not immediately; schedule it with effective_timing 'end_of_period'.")

    clock = db.one("SELECT now FROM clock LIMIT 1")
    now_ts = clock["now"] if clock else "2026-10-15T12:00:00Z"
    count = db.one("SELECT COUNT(*) as c FROM scheduled_changes", [])["c"]
    change_id = f"PC{1001 + count}"
    while db.one("SELECT 1 FROM scheduled_changes WHERE change_id = ?", [change_id]):
        count += 1
        change_id = f"PC{1001 + count}"

    if effective_timing == "immediate":
        # Taken now: the subscription changes in this call, so the change is recorded as applied,
        # not left pending with no date it would ever take effect.
        db.execute("UPDATE subscriptions SET plan_id = ?, seat_count = ?, version = version + 1 WHERE subscription_id = ?", [target_plan_id, target_seats, sub["subscription_id"]])
        db.execute("UPDATE renewal_terms SET renewal_price_cents = ? WHERE subscription_id = ?", [new_monthly, sub["subscription_id"]])
        effective_at, status = now_ts, "applied"
        message = f"{new_plan['name']} with {target_seats} seats is in effect now."
    else:
        effective_at, status = sub["current_period_end"], "pending"
        message = f"Successfully scheduled plan change to {new_plan['name']} effective at {effective_at}."
    db.execute("INSERT INTO scheduled_changes (change_id, tenant_id, target_plan_id, target_seats, effective_at, status, request_key, created_at, requested_by, effective_timing) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               [change_id, tenant_id, target_plan_id, target_seats, effective_at, status, request_key, now_ts, actor_id, effective_timing])

    return {
        "change_id": change_id,
        "tenant_id": tenant_id,
        "target_plan_id": target_plan_id,
        "target_seats": target_seats,
        "effective_at": effective_at,
        "status": status,
        "request_key": request_key,
        "message": message
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


_WRITES = ("scheduled_changes", "subscriptions", "renewal_terms")

def _all_or_nothing(db, run):
    """Run the call so that a failed call changes nothing. A handler gets no transaction of its own,
    so every table this tool writes is held first and put back if the call raises, whether a write
    failed partway or the call refused after writing. Only a fault the world simulates on purpose
    (an error marked keep_writes: a rival booking, a commit whose reply was lost, a fault used up)
    keeps what it wrote."""
    held = {table: db.query(f"SELECT * FROM {table}") for table in _WRITES}
    try:
        return run()
    except Exception as failed:
        if not getattr(failed, "keep_writes", False):
            for table, rows in held.items():
                if _same(db.query(f"SELECT * FROM {table}"), rows):
                    continue
                db.execute(f"DELETE FROM {table}")
                for row in rows:
                    cols = list(row)
                    db.execute(
                        f"INSERT INTO {table} ({', '.join(chr(34) + c + chr(34) for c in cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [row[c] for c in cols],
                    )
        raise

def _same(rows, held):
    """Whether a table still holds exactly the rows it held, in any order: only a table the call
    changed is rewritten."""
    import json

    def key(row):
        return json.dumps(row, sort_keys=True, default=str)

    return sorted(map(key, rows)) == sorted(map(key, held))


def handle(args, db):
    return _all_or_nothing(db, lambda: _handle(args, db))
