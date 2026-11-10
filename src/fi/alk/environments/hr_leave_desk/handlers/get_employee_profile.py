def handle(args, db):
    employee_id = args.get("employee_id")
    if not employee_id:
        raise ToolError("employee_id is required.")
    
    actor_id = args.get("actor_id")
    if actor_id and actor_id != employee_id:
        actor = db.one("SELECT * FROM employees WHERE employee_id = ?", (actor_id,))
        if not actor or (actor["role"] not in ("hr_business_partner", "hr_specialist", "hr_generalist", "director", "executive") and actor["employee_id"] not in ("M01", "M02", "M03", "M04")):
            emp_rec = db.one("SELECT manager_id FROM employees WHERE employee_id = ?", (employee_id,))
            if not emp_rec or emp_rec["manager_id"] != actor_id:
                raise ToolError(f"Permission denied: actor {actor_id} is not authorized to view profile of {employee_id}.")

    emp = db.one("SELECT employee_id, name, email, region, manager_id, role FROM employees WHERE employee_id = ?", (employee_id,))
    if not emp:
        raise ToolError(f"Employee {employee_id} not found.")
    
    return {
        "employee_id": emp["employee_id"],
        "name": emp["name"],
        "email": emp["email"],
        "region": emp["region"],
        "manager_id": emp["manager_id"],
        "role": emp["role"]
    }
