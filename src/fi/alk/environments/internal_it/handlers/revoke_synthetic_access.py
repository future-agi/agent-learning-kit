def handle(args, db):
    ent_id = args.get("entitlement_id")
    reason = args.get("reason")
    if not ent_id or not reason:
        raise ToolError("Missing required parameters: entitlement_id, reason")
    ent = db.one("SELECT * FROM entitlements WHERE entitlement_id = ?", [ent_id])
    if ent is None:
        raise ToolError(f"Entitlement {ent_id!r} not found.")
    actor = _act_for(db, ent["employee_id"])
    if ent["status"] != "active":
        raise ToolError(f"Entitlement {ent_id!r} is not currently active (status: {ent['status']}).")
    now = db.one("SELECT now FROM clock")["now"]
    db.execute("UPDATE entitlements SET status = 'revoked' WHERE entitlement_id = ?", [ent_id])
    _audit(db, "revoke_synthetic_access", actor, "entitlement", ent_id, f"Revoked {ent['role_name']} on {ent['app_id']} for {ent['employee_id']}. Reason: {reason}", now)
    return {
        "app_id": ent["app_id"],
        "employee_id": ent["employee_id"],
        "entitlement_id": ent_id,
        "message": "Access revoked successfully.",
        "reason": reason,
        "role_name": ent["role_name"],
        "status": "revoked"
    }


def _caller(db):
    """The employee signed in to this conversation. The session sets it; nothing the desk is told
    or passes as an argument changes it."""
    row = db.one("SELECT employee_id FROM session_caller WHERE id = 'default'")
    return row["employee_id"] if row else None


def _act_for(db, employee_id, also=()):
    """The signed-in caller, if they may act for or see `employee_id`'s access: the employee
    themselves, their manager, someone in `also` (the request's approvers), or an active member of
    IT operations. Naming an employee is not being them, so anyone else is refused."""
    caller = _caller(db)
    if caller and (caller == employee_id or caller in also):
        return caller
    me = db.one("SELECT department, status FROM employees WHERE employee_id = ?", [caller]) if caller else None
    if me and me["status"] == "active":
        if me["department"] == "it_operations":
            return caller
        emp = db.one("SELECT manager_id FROM employees WHERE employee_id = ?", [employee_id])
        if emp and emp["manager_id"] == caller:
            return caller
    raise ToolError(f"Permission denied: the person signed in to this conversation ({caller or 'nobody'}) may not act for or see the access of {employee_id}.", category="permission_denied")


def _audit(db, action, actor, target_type, target_id, details, now):
    n = db.one("SELECT COUNT(*) AS c FROM audit_log")["c"] + 1
    while db.one("SELECT 1 FROM audit_log WHERE log_id = ?", [f"LOG-{n:03d}"]):
        n += 1
    db.execute("INSERT INTO audit_log (log_id, action, actor_id, target_type, target_id, details, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)", [f"LOG-{n:03d}", action, actor, target_type, target_id, details, now])
