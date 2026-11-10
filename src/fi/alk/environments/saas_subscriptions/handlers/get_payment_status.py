def handle(args, db):
    if not args.get('tenant_id') or not args.get('actor_id') or not args.get('invoice_id'):
        raise ToolError("tenant_id, actor_id, invoice_id are required.")
    tenant_id = args.get("tenant_id")
    actor_id = args.get("actor_id")
    user = db.one("SELECT * FROM users WHERE tenant_id = ? AND actor_id = ?", [tenant_id, actor_id])
    if user is None:
        raise ToolError(f"User {actor_id!r} is not a user of tenant {tenant_id!r}.")
    if user["role"] != "billing_admin":
        raise ToolError("Permission denied: only a billing_admin of the tenant may do this.")
    inv = db.one("SELECT * FROM invoices WHERE tenant_id = ? AND invoice_id = ?", [tenant_id, args["invoice_id"]])
    if inv is None:
        raise ToolError(f"Invoice {args['invoice_id']!r} not found for tenant {tenant_id!r}.")
    pay = db.one("SELECT * FROM payments WHERE invoice_id = ?", [inv["invoice_id"]])
    if pay is None:
        raise ToolError(f"No payment recorded for {inv['invoice_id']}.")
    return {"invoice_id": inv["invoice_id"], "invoice_status": inv["status"], "payment_id": pay["payment_id"], "amount_cents": pay["amount_cents"], "status": pay["status"], "method": pay["method"], "card_last4": pay["card_last4"], "attempted_at": pay["attempted_at"], "next_retry_at": pay["next_retry_at"], "failure_reason": pay["failure_reason"]}
