def handle(args, db):
    if not args.get('tenant_id') or not args.get('actor_id'):
        raise ToolError("tenant_id, actor_id are required.")
    tenant_id = args.get("tenant_id")
    actor_id = args.get("actor_id")
    user = db.one("SELECT * FROM users WHERE tenant_id = ? AND actor_id = ?", [tenant_id, actor_id])
    if user is None:
        raise ToolError(f"User {actor_id!r} is not a user of tenant {tenant_id!r}.")
    target = args.get("user_id") or actor_id
    row = db.one("SELECT actor_id, name, role FROM users WHERE tenant_id = ? AND actor_id = ?", [tenant_id, target])
    if row is None:
        raise ToolError(f"User {target!r} is not a user of tenant {tenant_id!r}.")
    return {"user_id": row["actor_id"], "name": row["name"], "role": row["role"], "tenant_id": tenant_id}
