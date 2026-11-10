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
    inv = args.get("invoice_id")
    if inv:
        if db.one("SELECT 1 FROM invoices WHERE tenant_id = ? AND invoice_id = ?", [tenant_id, inv]) is None:
            raise ToolError(f"Invoice {inv!r} not found for tenant {tenant_id!r}.")
        rows = db.query("SELECT * FROM credit_notes WHERE tenant_id = ? AND invoice_id = ? ORDER BY created_at", [tenant_id, inv])
    else:
        rows = db.query("SELECT * FROM credit_notes WHERE tenant_id = ? ORDER BY created_at", [tenant_id])
    return {"tenant_id": tenant_id, "credit_notes": rows, "count": len(rows)}
