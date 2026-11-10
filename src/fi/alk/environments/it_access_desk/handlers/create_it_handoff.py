def handle(args, db):
    emp_id = args.get("employee_id")
    issue_type = args.get("issue_type")
    details = args.get("details")
    if not emp_id or not issue_type or not details:
        raise ToolError("Missing required parameters: employee_id, issue_type, details")
        
    emp = db.one("SELECT * FROM employees WHERE employee_id = ?", [emp_id])
    if emp is None:
        raise ToolError(f"Employee {emp_id!r} not found.")
        
    now_row = db.one("SELECT now FROM clock")
    now = now_row["now"] if now_row else "2026-10-26T09:00:00Z"
    
    count_row = db.one("SELECT COUNT(*) as c FROM handoff_tickets")
    ticket_num = count_row["c"] + 1
    ticket_id = f"TICK-{ticket_num:03d}"
    
    db.execute("INSERT INTO handoff_tickets (ticket_id, employee_id, issue_type, details, status, created_at) VALUES (?, ?, ?, ?, 'open', ?)", [ticket_id, emp_id, issue_type, details, now])
    
    log_count = db.one("SELECT COUNT(*) as c FROM audit_log")["c"] + 1
    db.execute("INSERT INTO audit_log (log_id, action, actor_id, target_type, target_id, details, timestamp) VALUES (?, 'create_it_handoff', ?, 'handoff_ticket', ?, ?, ?)", [f"LOG-{log_count:03d}", emp_id, ticket_id, f"Escalated {issue_type}: {details}", now])
    
    return {
        "created_at": now,
        "employee_id": emp_id,
        "issue_type": issue_type,
        "message": "IT escalation handoff ticket created successfully.",
        "status": "open",
        "ticket_id": ticket_id
    }