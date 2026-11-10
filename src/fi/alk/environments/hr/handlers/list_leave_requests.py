def _may_view(db, actor_id, employee_id):
    """The employee themselves, their manager, a manager or director above them, or HR."""
    if not actor_id or actor_id == employee_id:
        return True
    actor = db.one("SELECT * FROM employees WHERE employee_id = ?", (actor_id,))
    if not actor:
        return False
    if actor["role"] in ("hr_business_partner", "hr_specialist", "hr_generalist"):
        return True
    seen, current = set(), employee_id
    while current and current not in seen:
        seen.add(current)
        row = db.one("SELECT manager_id FROM employees WHERE employee_id = ?", (current,))
        if not row:
            return False
        if row["manager_id"] == actor_id:
            return True
        if row["manager_id"] == current:
            return False
        current = row["manager_id"]
    return False

def handle(args, db):
    employee_id = args.get("employee_id")
    actor_id = args.get("actor_id")
    status = args.get("status")
    if not employee_id:
        raise ToolError("employee_id is required.")
    if not db.one("SELECT 1 FROM employees WHERE employee_id = ?", (employee_id,)):
        raise ToolError(f"Employee {employee_id} not found.")
    if not _may_view(db, actor_id, employee_id):
        raise ToolError(f"Permission denied: actor {actor_id} may not view the requests of {employee_id}.")
    if status:
        rows = db.query("SELECT * FROM leave_requests WHERE employee_id = ? AND status = ? ORDER BY start_date", (employee_id, status))
    else:
        rows = db.query("SELECT * FROM leave_requests WHERE employee_id = ? ORDER BY start_date", (employee_id,))
    return {"employee_id": employee_id, "requests": [{"request_id": r["request_id"], "leave_type": r["leave_type"], "start_date": r["start_date"], "end_date": r["end_date"], "working_days": float(r["working_days"]), "status": r["status"], "approver_id": r["approver_id"]} for r in rows], "count": len(rows)}
