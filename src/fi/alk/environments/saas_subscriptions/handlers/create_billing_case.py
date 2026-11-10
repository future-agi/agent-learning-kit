def handle(args, db):
    if not args.get('tenant_id') or not args.get('actor_id') or not args.get('category') or not args.get('summary') or not args.get('request_key'):
        raise ToolError("tenant_id, actor_id, category, summary, request_key are required.")
    tenant_id = args.get("tenant_id")
    actor_id = args.get("actor_id")
    user = db.one("SELECT * FROM users WHERE tenant_id = ? AND actor_id = ?", [tenant_id, actor_id])
    if user is None:
        raise ToolError(f"User {actor_id!r} is not a user of tenant {tenant_id!r}.")
    category = args["category"]
    if category not in ("refund", "waiver", "discount", "dispute", "payment", "other"):
        raise ToolError("category must be one of refund, waiver, discount, dispute, payment, other.")
    inv = args.get("invoice_id")
    if inv and db.one("SELECT 1 FROM invoices WHERE tenant_id = ? AND invoice_id = ?", [tenant_id, inv]) is None:
        raise ToolError(f"Invoice {inv!r} not found for tenant {tenant_id!r}.")
    existing = db.one("SELECT * FROM billing_cases WHERE tenant_id = ? AND request_key = ?", [tenant_id, args["request_key"]])
    if existing:
        return {"case_id": existing["case_id"], "status": existing["status"], "category": existing["category"], "invoice_id": existing["invoice_id"], "idempotent": True}
    now = db.one("SELECT now FROM clock")["now"]
    n = db.one("SELECT COUNT(*) AS c FROM billing_cases")["c"]
    case_id = f"BC-{301 + n}"
    db.execute("INSERT INTO billing_cases (case_id, tenant_id, opened_by, category, invoice_id, summary, status, request_key, created_at, updated_at, resolution) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, NULL)", [case_id, tenant_id, actor_id, category, inv, args["summary"], args["request_key"], now, now])
    return {"case_id": case_id, "status": "open", "category": category, "invoice_id": inv, "idempotent": False, "message": "Finance decides the case; nothing has been credited or refunded."}
