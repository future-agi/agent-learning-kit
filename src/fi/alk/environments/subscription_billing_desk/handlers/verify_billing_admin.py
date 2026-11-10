def handle(args, db):
    tenant_id = args.get("tenant_id")
    actor_id = args.get("actor_id")
    if not tenant_id or not actor_id:
        raise ToolError("tenant_id and actor_id are required.")
    user = db.one("SELECT * FROM users WHERE tenant_id = ? AND actor_id = ?", [tenant_id, actor_id])
    if user is None:
        raise ToolError(f"User {actor_id!r} not found for tenant {tenant_id!r}.")
    is_admin = (user["role"] == "billing_admin")
    return {"is_admin": is_admin, "role": user["role"], "actor_name": user["name"]}