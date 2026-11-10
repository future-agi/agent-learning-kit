def handle(args, db):
    if not args.get('tenant_id') or not args.get('actor_id') or not args.get('name') or not args.get('email'):
        raise ToolError("tenant_id, actor_id, name, email are required.")
    tenant_id = args.get("tenant_id")
    actor_id = args.get("actor_id")
    user = db.one("SELECT * FROM users WHERE tenant_id = ? AND actor_id = ?", [tenant_id, actor_id])
    if user is None:
        raise ToolError(f"User {actor_id!r} is not a user of tenant {tenant_id!r}.")
    if user["role"] != "billing_admin":
        raise ToolError("Permission denied: only a billing_admin of the tenant may do this.")
    if str(args.get("confirmation_code") or "").strip().lower() not in ("confirmed", "yes", "true", "approved", "confirm"):
        raise ToolError("Explicit confirmation from the billing admin is required first.")
    import re
    email = str(args["email"]).strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ToolError(f"{email!r} is not a valid email address.")
    now = db.one("SELECT now FROM clock")["now"]
    if db.one("SELECT 1 FROM billing_contacts WHERE tenant_id = ?", [tenant_id]):
        db.execute("UPDATE billing_contacts SET name = ?, email = ?, updated_at = ?, updated_by = ? WHERE tenant_id = ?", [args["name"], email, now, actor_id, tenant_id])
    else:
        db.execute("INSERT INTO billing_contacts (tenant_id, name, email, updated_at, updated_by) VALUES (?, ?, ?, ?, ?)", [tenant_id, args["name"], email, now, actor_id])
    return {"tenant_id": tenant_id, "name": args["name"], "email": email, "updated_at": now}
