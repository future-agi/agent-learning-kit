def handle(args, db):
    tenant_id = args.get("tenant_id")
    change_id = args.get("change_id")
    actor_id = args.get("actor_id")
    if not tenant_id or not change_id or not actor_id:
        raise ToolError("tenant_id, change_id, and actor_id are required.")
    user = _entitled(db, tenant_id, actor_id, "admin")
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
