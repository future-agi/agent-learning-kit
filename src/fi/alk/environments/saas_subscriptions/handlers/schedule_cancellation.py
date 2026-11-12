def _handle(args, db):
    if not args.get('tenant_id') or not args.get('actor_id') or not args.get('effective_timing') or not args.get('request_key'):
        raise ToolError("tenant_id, actor_id, effective_timing, request_key are required.")
    tenant_id = args.get("tenant_id")
    actor_id = args.get("actor_id")
    user = _entitled(db, tenant_id, actor_id, "admin")
    if args["effective_timing"] == "immediate":
        raise ToolError("Immediate cancellation is not available: a subscription is cancelled at the end of its current term, and access continues until then.")
    if args["effective_timing"] != "end_of_term":
        raise ToolError("effective_timing must be 'end_of_term'.")
    gate = db.one("SELECT value FROM environment_policy WHERE name = 'require_confirmation'")
    if (gate is None or gate["value"] == "true") and str(args.get("confirmation_code") or "").strip().lower() not in ("confirmed", "yes", "true", "approved", "confirm"):
        raise ToolError("Explicit confirmation from the billing admin is required first.")
    existing = db.one("SELECT * FROM cancellation_requests WHERE tenant_id = ? AND (request_key = ? OR status = 'scheduled')", [tenant_id, args["request_key"]])
    if existing and existing["request_key"] == args["request_key"] and existing["requested_by"] != actor_id:
        # A reused key is a replay only for the admin who made the request.
        raise ToolError(f"request_key {args['request_key']!r} belongs to another person's request; use a new key.", category="conflict")
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


_WRITES = ("cancellation_requests", "subscriptions")

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
