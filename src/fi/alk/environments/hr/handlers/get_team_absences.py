def _caller(db, claimed=None):
    """The person signed in to this conversation, from the session and never from an argument. An
    actor the agent names must be that person: naming someone is not being them."""
    row = db.one("SELECT employee_id FROM session_caller WHERE id = 'default'")
    who = None
    if row and row["employee_id"]:
        who = db.one("SELECT * FROM employees WHERE employee_id = ?", (row["employee_id"],))
    if not who:
        raise ToolError("Permission denied: nobody is signed in to this conversation.")
    if claimed and claimed != who["employee_id"]:
        raise ToolError(f"Permission denied: {claimed} is not the person signed in to this conversation ({who['employee_id']}).")
    return who


def handle(args, db):
    manager_id = args.get("manager_id")
    start = args.get("start_date")
    end = args.get("end_date")
    if not manager_id or not start or not end:
        raise ToolError("manager_id, start_date and end_date are required.")
    # The team view is the signed-in manager's own: naming a manager does not open theirs.
    _caller(db, manager_id)
    team = db.query("SELECT employee_id, name FROM employees WHERE manager_id = ? AND employee_id != ?", (manager_id, manager_id))
    if not team:
        raise ToolError(f"Permission denied: {manager_id} manages no one, so there is no team view.")
    ids = {t["employee_id"]: t["name"] for t in team}
    rows = db.query("SELECT * FROM leave_requests WHERE status IN ('pending', 'approved') AND NOT (end_date < ? OR start_date > ?) ORDER BY start_date", (start, end))
    absences = [{"employee_id": r["employee_id"], "name": ids[r["employee_id"]], "start_date": r["start_date"], "end_date": r["end_date"], "status": r["status"]} for r in rows if r["employee_id"] in ids]
    return {"manager_id": manager_id, "start_date": start, "end_date": end, "absences": absences, "note": "Dates and status only; reasons and leave types are not shown."}
