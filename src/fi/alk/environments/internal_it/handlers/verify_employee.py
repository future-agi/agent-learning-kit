def handle(args, db):
    emp_id = args.get("employee_id")
    if not emp_id:
        raise ToolError("Missing required parameter: employee_id")
    emp = db.one("SELECT * FROM employees WHERE employee_id = ? OR email = ?", [emp_id, emp_id])
    if emp is None:
        raise ToolError(f"Employee {emp_id!r} not found in directory.")
    # A directory lookup: anyone may look anyone up. Whether this record is the person signed in
    # to the conversation is said apart, since a name or ID typed in chat proves nothing.
    caller = db.one("SELECT employee_id FROM session_caller WHERE id = 'default'")
    return {
        "employee_id": emp["employee_id"],
        "name": emp["name"],
        "email": emp["email"],
        "department": emp["department"],
        "manager_id": emp["manager_id"],
        "status": emp["status"],
        "signed_in": bool(caller and caller["employee_id"] == emp["employee_id"])
    }
