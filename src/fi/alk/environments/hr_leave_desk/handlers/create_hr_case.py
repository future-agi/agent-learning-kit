def handle(args, db):
    employee_id = args.get("employee_id")
    category = args.get("category")
    subject = args.get("subject")
    description = args.get("description")
    actor_id = args.get("actor_id")
    
    if not employee_id or not category or not subject or not description:
        raise ToolError("employee_id, category, subject, and description are required.")
        
    if actor_id and actor_id != employee_id:
        raise ToolError(f"Permission denied: actor {actor_id} cannot open HR cases on behalf of employee {employee_id}.")
        
    emp = db.one("SELECT employee_id FROM employees WHERE employee_id = ?", (employee_id,))
    if not emp:
        raise ToolError(f"Employee {employee_id} not found.")
        
    count_row = db.one("SELECT COUNT(*) as cnt FROM hr_cases")
    count = count_row["cnt"] if count_row else 0
    case_id = f"CASE-{3001 + count}"
    
    clock_row = db.one("SELECT now FROM clock")
    now_str = clock_row["now"] if clock_row else "2026-10-26T09:00:00Z"
    status = "open"
    
    db.execute(
        "INSERT INTO hr_cases (case_id, employee_id, category, subject, description, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (case_id, employee_id, category, subject, description, status, now_str)
    )
    
    return {
        "case_id": case_id,
        "employee_id": employee_id,
        "category": category,
        "subject": subject,
        "description": description,
        "status": status,
        "created_at": now_str
    }
