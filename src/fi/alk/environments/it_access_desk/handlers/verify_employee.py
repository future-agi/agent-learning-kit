def handle(args, db):
    emp_id = args.get("employee_id")
    if not emp_id:
        raise ToolError("Missing required parameter: employee_id")
    emp = db.one("SELECT * FROM employees WHERE employee_id = ? OR email = ?", [emp_id, emp_id])
    if emp is None:
        raise ToolError(f"Employee {emp_id!r} not found in directory.")
    return {
        "employee_id": emp["employee_id"],
        "name": emp["name"],
        "email": emp["email"],
        "department": emp["department"],
        "manager_id": emp["manager_id"],
        "status": emp["status"]
    }