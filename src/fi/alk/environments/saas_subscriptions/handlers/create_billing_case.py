def _handle(args, db):
    if not args.get('tenant_id') or not args.get('actor_id') or not args.get('category') or not args.get('summary') or not args.get('request_key'):
        raise ToolError("tenant_id, actor_id, category, summary, request_key are required.")
    tenant_id = args.get("tenant_id")
    actor_id = args.get("actor_id")
    user = _entitled(db, tenant_id, actor_id, "member")
    category = args["category"]
    if category not in ("refund", "waiver", "discount", "dispute", "payment", "other"):
        raise ToolError("category must be one of refund, waiver, discount, dispute, payment, other.")
    inv = args.get("invoice_id")
    if inv and db.one("SELECT 1 FROM invoices WHERE tenant_id = ? AND invoice_id = ?", [tenant_id, inv]) is None:
        raise ToolError(f"Invoice {inv!r} not found for tenant {tenant_id!r}.")
    existing = db.one("SELECT * FROM billing_cases WHERE tenant_id = ? AND request_key = ?", [tenant_id, args["request_key"]])
    if existing and (existing["opened_by"] != actor_id or existing["category"] != category or existing["invoice_id"] != inv):
        # A replay returns the case already opened only for the same person and the same request.
        raise ToolError(f"request_key {args['request_key']!r} was already used for {existing['case_id']} ({existing['category']}, {existing['invoice_id']}); a different request needs a new key.", category="conflict")
    if existing:
        return {"case_id": existing["case_id"], "status": existing["status"], "category": existing["category"], "invoice_id": existing["invoice_id"], "idempotent": True}
    now = db.one("SELECT now FROM clock")["now"]
    n = db.one("SELECT COUNT(*) AS c FROM billing_cases")["c"]
    case_id = f"BC-{301 + n}"
    db.execute("INSERT INTO billing_cases (case_id, tenant_id, opened_by, category, invoice_id, summary, status, request_key, created_at, updated_at, resolution) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, NULL)", [case_id, tenant_id, actor_id, category, inv, args["summary"], args["request_key"], now, now])
    return {"case_id": case_id, "status": "open", "category": category, "invoice_id": inv, "idempotent": False, "message": "Finance decides the case; nothing has been credited or refunded."}


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


_WRITES = ("billing_cases",)

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
