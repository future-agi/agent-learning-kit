def handle(args, db):
    if not args.get('tenant_id') or not args.get('actor_id') or not args.get('case_id'):
        raise ToolError("tenant_id, actor_id, case_id are required.")
    tenant_id = args.get("tenant_id")
    actor_id = args.get("actor_id")
    user = db.one("SELECT * FROM users WHERE tenant_id = ? AND actor_id = ?", [tenant_id, actor_id])
    if user is None:
        raise ToolError(f"User {actor_id!r} is not a user of tenant {tenant_id!r}.")
    case = db.one("SELECT * FROM billing_cases WHERE tenant_id = ? AND case_id = ?", [tenant_id, args["case_id"]])
    if case is None:
        raise ToolError(f"Billing case {args['case_id']!r} not found for tenant {tenant_id!r}.")
    return {"case_id": case["case_id"], "category": case["category"], "invoice_id": case["invoice_id"], "status": case["status"], "resolution": case["resolution"], "updated_at": case["updated_at"]}
