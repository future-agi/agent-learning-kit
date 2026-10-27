def handle(args, db):
    tenant_id = args.get("tenant_id")
    change_id = args.get("change_id")
    actor_id = args.get("actor_id")
    if not tenant_id or not change_id or not actor_id:
        raise ToolError("tenant_id, change_id, and actor_id are required.")
    user = db.one("SELECT * FROM users WHERE tenant_id = ? AND actor_id = ?", [tenant_id, actor_id])
    if user is None:
        raise ToolError(f"User {actor_id!r} not found in tenant {tenant_id!r}.")
    if user["role"] != "billing_admin":
        raise ToolError("Permission denied: only billing_admin can cancel scheduled changes.")
    change = db.one("SELECT * FROM scheduled_changes WHERE tenant_id = ? AND change_id = ?", [tenant_id, change_id])
    if change is None:
        raise ToolError(f"Scheduled change {change_id!r} not found for tenant {tenant_id!r}.")
    if change["status"] != "pending":
        raise ToolError(f"Scheduled change {change_id!r} is not in pending status (current status: {change['status']}).")
    db.execute("UPDATE scheduled_changes SET status = 'canceled' WHERE tenant_id = ? AND change_id = ?", [tenant_id, change_id])
    return {
        "change_id": change_id,
        "tenant_id": tenant_id,
        "status": "canceled",
        "message": f"Scheduled change {change_id} has been canceled."
    }