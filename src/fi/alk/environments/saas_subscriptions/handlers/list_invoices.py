def handle(args, db):
    if not args.get('tenant_id') or not args.get('actor_id'):
        raise ToolError("tenant_id, actor_id are required.")
    tenant_id = args.get("tenant_id")
    actor_id = args.get("actor_id")
    user = db.one("SELECT * FROM users WHERE tenant_id = ? AND actor_id = ?", [tenant_id, actor_id])
    if user is None:
        raise ToolError(f"User {actor_id!r} is not a user of tenant {tenant_id!r}.")
    if user["role"] != "billing_admin":
        raise ToolError("Permission denied: only a billing_admin of the tenant may do this.")
    status = args.get("status")
    if status and status not in ("paid", "open"):
        raise ToolError("status must be 'paid' or 'open'.")
    sql = "SELECT invoice_id, period_start, period_end, subtotal_cents, tax_cents, total_cents, status, created_at FROM invoices WHERE tenant_id = ?"
    params = [tenant_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    rows = db.query(sql + " ORDER BY created_at DESC, invoice_id", params)
    return {"tenant_id": tenant_id, "invoices": rows, "count": len(rows)}
