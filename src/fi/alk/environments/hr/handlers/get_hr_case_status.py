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


def _sees(caller, employee_id):
    """Whether the caller may see `employee_id`'s private leave records in full (balances, reasons,
    case notes): their own, or HR. A manager sees dates and status only, never balances or reasons."""
    return caller["employee_id"] == employee_id or caller["role"] in _HR_ROLES


def handle(args, db):
    case_id = args.get("case_id")
    employee_id = args.get("employee_id")
    if not case_id or not employee_id:
        raise ToolError("case_id and employee_id are required.")
    # employee_id is the person asking, which only the session can say.
    caller = _caller(db, employee_id)
    case = db.one("SELECT * FROM hr_cases WHERE case_id = ?", (case_id,))
    if not case:
        raise ToolError(f"HR case {case_id} not found.")
    if not _sees(caller, case["employee_id"]):
        raise ToolError(f"Permission denied: case {case_id} is not {employee_id}'s.")
    return {"case_id": case_id, "employee_id": case["employee_id"], "category": case["category"], "subject": case["subject"], "status": case["status"], "created_at": case["created_at"], "updated_at": case["updated_at"], "latest_note": case["notes"] or ""}
