def handle(args, db):
    case_id = args.get("case_id")
    employee_id = args.get("employee_id")
    note = args.get("note")
    if not case_id or not employee_id or not note:
        raise ToolError("case_id, employee_id and note are required.")
    case = db.one("SELECT * FROM hr_cases WHERE case_id = ?", (case_id,))
    if not case:
        raise ToolError(f"HR case {case_id} not found.")
    if case["employee_id"] != employee_id:
        raise ToolError(f"Permission denied: case {case_id} is not {employee_id}'s.")
    if case["status"] == "resolved":
        raise ToolError(f"Case {case_id} is resolved; open a new case instead.")
    now = db.one("SELECT now FROM clock")["now"]
    notes = ((case["notes"] or "") + f" | {employee_id}: {note}").strip(" |")
    db.execute("UPDATE hr_cases SET notes = ?, updated_at = ? WHERE case_id = ?", (notes, now, case_id))
    return {"case_id": case_id, "status": case["status"], "updated_at": now, "notes": notes}
