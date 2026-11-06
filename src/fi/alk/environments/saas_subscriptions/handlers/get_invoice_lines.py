def handle(args, db):
    tenant_id = args.get("tenant_id")
    invoice_id = args.get("invoice_id")
    actor_id = args.get("actor_id")
    if not tenant_id or not invoice_id or not actor_id:
        raise ToolError("tenant_id, invoice_id, and actor_id are required.")
    user = db.one("SELECT * FROM users WHERE tenant_id = ? AND actor_id = ?", [tenant_id, actor_id])
    if user is None:
        raise ToolError(f"User {actor_id!r} not found in tenant {tenant_id!r}.")
    if user["role"] != "billing_admin":
        raise ToolError("Permission denied: only billing_admin can view invoice line items.")
    inv = db.one("SELECT * FROM invoices WHERE tenant_id = ? AND invoice_id = ?", [tenant_id, invoice_id])
    if inv is None:
        raise ToolError(f"Invoice {invoice_id!r} not found for tenant {tenant_id!r}.")
    lines = db.query("SELECT line_id, description, quantity, unit_amount_cents, amount_cents, line_type FROM invoice_lines WHERE invoice_id = ?", [invoice_id])
    return {
        "invoice_id": inv["invoice_id"],
        "tenant_id": inv["tenant_id"],
        "period_start": inv["period_start"],
        "period_end": inv["period_end"],
        "subtotal_cents": inv["subtotal_cents"],
        "tax_cents": inv["tax_cents"],
        "total_cents": inv["total_cents"],
        "status": inv["status"],
        "lines": lines
    }