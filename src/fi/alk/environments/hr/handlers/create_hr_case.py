_HR_ROLES = ("hr_business_partner", "hr_specialist", "hr_generalist")


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


def _acts_for(db, caller, employee_id):
    """Whether the caller may change `employee_id`'s leave or cases: their own, or as the HR partner
    the world names for that employee's region (the approval matrix gives the partner the exceptions)."""
    if caller["employee_id"] == employee_id:
        return True
    return db.one(
        "SELECT 1 AS ok FROM hr_partners p JOIN employees e ON e.region = p.region WHERE e.employee_id = ? AND p.employee_id = ?",
        (employee_id, caller["employee_id"]),
    ) is not None


def handle(args, db):
    employee_id = args.get("employee_id")
    category = args.get("category")
    subject = args.get("subject")
    description = args.get("description")
    actor_id = args.get("actor_id")
    
    if not employee_id or not category or not subject or not description:
        raise ToolError("employee_id, category, subject, and description are required.")
        
    caller = _caller(db, actor_id)
    emp = db.one("SELECT employee_id FROM employees WHERE employee_id = ?", (employee_id,))
    if not emp:
        raise ToolError(f"Employee {employee_id} not found.")
    if not _acts_for(db, caller, employee_id):
        raise ToolError(f"Permission denied: {caller['employee_id']} cannot open HR cases on behalf of employee {employee_id}.")
        
    count_row = db.one("SELECT COUNT(*) as cnt FROM hr_cases")
    number = 3001 + (count_row["cnt"] if count_row else 0)
    # A scenario may already hold a case with the next number; never reuse one.
    while db.one("SELECT 1 FROM hr_cases WHERE case_id = ?", (f"CASE-{number}",)):
        number += 1
    case_id = f"CASE-{number}"
    
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
