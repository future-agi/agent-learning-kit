def handle(args, db):
    emp_id = args.get("employee_id")
    issue_type = args.get("issue_type")
    details = args.get("details")
    if not emp_id or not issue_type or not details:
        raise ToolError("Missing required parameters: employee_id, issue_type, details")
    emp = db.one("SELECT * FROM employees WHERE employee_id = ?", [emp_id])
    if emp is None:
        raise ToolError(f"Employee {emp_id!r} not found.")
    actor = _act_for(db, emp_id)
    now = db.one("SELECT now FROM clock")["now"]
    n = db.one("SELECT COUNT(*) as c FROM handoff_tickets")["c"] + 1
    while db.one("SELECT 1 FROM handoff_tickets WHERE ticket_id = ?", [f"TICK-{n:03d}"]):
        n += 1
    ticket_id = f"TICK-{n:03d}"
    db.execute("INSERT INTO handoff_tickets (ticket_id, employee_id, issue_type, details, status, created_at) VALUES (?, ?, ?, ?, 'open', ?)", [ticket_id, emp_id, issue_type, details, now])
    _audit(db, "create_it_handoff", actor, "handoff_ticket", ticket_id, f"Escalated {issue_type} for {emp_id}: {details}", now)
    return {
        "created_at": now,
        "employee_id": emp_id,
        "issue_type": issue_type,
        "message": "IT escalation handoff ticket created successfully.",
        "status": "open",
        "ticket_id": ticket_id
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
