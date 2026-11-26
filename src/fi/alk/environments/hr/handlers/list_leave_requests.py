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


def _manages(db, caller_id, employee_id):
    """Whether the caller is above `employee_id` in the reporting line."""
    seen, current = set(), employee_id
    while current and current not in seen:
        seen.add(current)
        row = db.one("SELECT manager_id FROM employees WHERE employee_id = ?", (current,))
        if not row or row["manager_id"] == current:
            return False
        if row["manager_id"] == caller_id:
            return True
        current = row["manager_id"]
    return False


def handle(args, db):
    employee_id = args.get("employee_id")
    status = args.get("status")
    if not employee_id:
        raise ToolError("employee_id is required.")
    caller = _caller(db, args.get("actor_id"))
    if not db.one("SELECT 1 FROM employees WHERE employee_id = ?", (employee_id,)):
        raise ToolError(f"Employee {employee_id} not found.")
    full = _sees(caller, employee_id)
    if not full and not _manages(db, caller["employee_id"], employee_id):
        raise ToolError(f"Permission denied: {caller['employee_id']} may not view the requests of {employee_id}.")
    if status:
        rows = db.query("SELECT * FROM leave_requests WHERE employee_id = ? AND status = ? ORDER BY start_date", (employee_id, status))
    else:
        rows = db.query("SELECT * FROM leave_requests WHERE employee_id = ? ORDER BY start_date", (employee_id,))
    requests = []
    for r in rows:
        one = {"request_id": r["request_id"], "leave_type": r["leave_type"], "start_date": r["start_date"], "end_date": r["end_date"], "working_days": float(r["working_days"]), "status": r["status"], "approver_id": r["approver_id"]}
        if not full:
            # A manager above sees dates and status; the leave type can be medical.
            del one["leave_type"]
        requests.append(one)
    return {"employee_id": employee_id, "requests": requests, "count": len(rows)}
