def handle(args, db):
    ent_id = args.get("entitlement_id")
    reason = args.get("reason")
    if not ent_id or not reason:
        raise ToolError("Missing required parameters: entitlement_id, reason")
        
    ent = db.one("SELECT * FROM entitlements WHERE entitlement_id = ?", [ent_id])
    if ent is None:
        raise ToolError(f"Entitlement {ent_id!r} not found.")
        
    if ent["status"] != "active":
        raise ToolError(f"Entitlement {ent_id!r} is not currently active (status: {ent['status']}).")
        
    now_row = db.one("SELECT now FROM clock")
    now = now_row["now"] if now_row else "2026-10-26T09:00:00Z"
    
    db.execute("UPDATE entitlements SET status = 'revoked' WHERE entitlement_id = ?", [ent_id])
    
    log_count = db.one("SELECT COUNT(*) as c FROM audit_log")["c"] + 1
    db.execute("INSERT INTO audit_log (log_id, action, actor_id, target_type, target_id, details, timestamp) VALUES (?, 'revoke_synthetic_access', 'system', 'entitlement', ?, ?, ?)", [f"LOG-{log_count:03d}", ent_id, f"Revoked {ent['role_name']} on {ent['app_id']} for {ent['employee_id']}. Reason: {reason}", now])
    
    return {
        "app_id": ent["app_id"],
        "employee_id": ent["employee_id"],
        "entitlement_id": ent_id,
        "message": "Access revoked successfully.",
        "reason": reason,
        "role_name": ent["role_name"],
        "status": "revoked"
    }