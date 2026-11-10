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
    case_id = args.get("case_id")
    employee_id = args.get("employee_id")
    if not case_id or not employee_id:
        raise ToolError("case_id and employee_id are required.")
    case = db.one("SELECT * FROM hr_cases WHERE case_id = ?", (case_id,))
    if not case:
        raise ToolError(f"HR case {case_id} not found.")
    if case["employee_id"] != employee_id and not _may_view(db, employee_id, case["employee_id"]):
        raise ToolError(f"Permission denied: case {case_id} is not {employee_id}'s.")
    return {"case_id": case_id, "employee_id": case["employee_id"], "category": case["category"], "subject": case["subject"], "status": case["status"], "created_at": case["created_at"], "updated_at": case["updated_at"], "latest_note": case["notes"] or ""}
