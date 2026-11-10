def handle(args, db):
    manager_id = args.get("manager_id")
    start = args.get("start_date")
    end = args.get("end_date")
    if not manager_id or not start or not end:
        raise ToolError("manager_id, start_date and end_date are required.")
    mgr = db.one("SELECT * FROM employees WHERE employee_id = ?", (manager_id,))
    if not mgr:
        raise ToolError(f"Employee {manager_id} not found.")
    team = db.query("SELECT employee_id, name FROM employees WHERE manager_id = ? AND employee_id != ?", (manager_id, manager_id))
    if not team:
        raise ToolError(f"Permission denied: {manager_id} manages no one, so there is no team view.")
    ids = {t["employee_id"]: t["name"] for t in team}
    rows = db.query("SELECT * FROM leave_requests WHERE status IN ('pending', 'approved') AND NOT (end_date < ? OR start_date > ?) ORDER BY start_date", (start, end))
    absences = [{"employee_id": r["employee_id"], "name": ids[r["employee_id"]], "start_date": r["start_date"], "end_date": r["end_date"], "status": r["status"]} for r in rows if r["employee_id"] in ids]
    return {"manager_id": manager_id, "start_date": start, "end_date": end, "absences": absences, "note": "Dates and status only; reasons and leave types are not shown."}
