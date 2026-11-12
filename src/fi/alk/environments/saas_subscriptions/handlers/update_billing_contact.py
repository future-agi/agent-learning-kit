def handle(args, db):
    if not args.get('tenant_id') or not args.get('actor_id') or not args.get('name') or not args.get('email'):
        raise ToolError("tenant_id, actor_id, name, email are required.")
    tenant_id = args.get("tenant_id")
    actor_id = args.get("actor_id")
    user = _entitled(db, tenant_id, actor_id, "admin")
    gate = db.one("SELECT value FROM environment_policy WHERE name = 'require_confirmation'")
    if (gate is None or gate["value"] == "true") and str(args.get("confirmation_code") or "").strip().lower() not in ("confirmed", "yes", "true", "approved", "confirm"):
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


def _entitled(db, tenant_id, actor_id, need):
    """Who the desk acts for is decided by who is signed in to the conversation (session_caller),
    never by the actor_id the agent passes: actor_id must be that caller, and it grants only what the
    world records for them. `need` is "self" (the caller is a user of the tenant: their own role),
    "member" (any user of the tenant, or Cloudnest staff entitled to the tenant), "read" (the tenant's
    billing admin, or entitled staff: invoices, payments, credits, terms and the user list) or "admin"
    (the tenant's billing admin only: every change to the subscription or the billing contact).
    Staff are entitled by role (billing lead, billing support, billing operations, finance: every
    tenant) or as the account manager the tenant is assigned to; staff never change a subscription."""
    row = db.one("SELECT actor_id FROM session_caller WHERE id = 'default'")
    caller = row["actor_id"] if row else None
    if not caller or actor_id != caller:
        raise ToolError(f"Permission denied: {actor_id!r} is not the person signed in to this conversation ({caller or 'nobody'}); the desk acts only for the caller.")
    user = db.one("SELECT * FROM users WHERE tenant_id = ? AND actor_id = ?", [tenant_id, actor_id])
    if user is not None:
        if need in ("read", "admin") and user["role"] != "billing_admin":
            raise ToolError("Permission denied: only a billing_admin of the tenant may do this.")
        return user
    staff = db.one("SELECT * FROM slack_users WHERE user_id = ?", [actor_id])
    if staff is not None and need in ("member", "read"):
        desk = staff["role"] in ("billing_lead", "billing_support", "billing_operations", "finance")
        assigned = db.one("SELECT 1 FROM account_assignments WHERE tenant_id = ? AND user_id = ?", [tenant_id, actor_id])
        if desk or assigned:
            return {"actor_id": actor_id, "tenant_id": tenant_id, "name": staff["name"], "email": staff["email"], "role": staff["role"]}
    if staff is not None:
        raise ToolError(f"Permission denied: {actor_id!r} is Cloudnest staff, not a user of tenant {tenant_id!r}; only the tenant's billing admin may do this.")
    raise ToolError(f"User {actor_id!r} is not a user of tenant {tenant_id!r}.")
