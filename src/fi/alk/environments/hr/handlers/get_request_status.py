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
    request_id = args.get("request_id")
    if not request_id:
        raise ToolError("request_id is required.")
    caller = _caller(db, args.get("actor_id"))
    req = db.one("SELECT * FROM leave_requests WHERE request_id = ?", (request_id,))
    if not req:
        raise ToolError(f"Leave request {request_id} not found.")
    full = _sees(caller, req["employee_id"])
    # The approver and the managers above see the request's dates and status, never its reason or
    # leave type, which can be medical.
    if not full and caller["employee_id"] != req["approver_id"] and not _manages(db, caller["employee_id"], req["employee_id"]):
        raise ToolError(f"Permission denied: {caller['employee_id']} may not view request {request_id}.")
    out = {
        "request_id": req["request_id"],
        "employee_id": req["employee_id"],
        "leave_type": req["leave_type"],
        "start_date": req["start_date"],
        "end_date": req["end_date"],
        "working_days": float(req["working_days"]),
        "status": req["status"],
        "approver_id": req["approver_id"],
        "reason": req["reason"],
        "created_at": req["created_at"]
    }
    if not full:
        del out["leave_type"], out["reason"]
        out["note"] = "Dates and status only; the reason and leave type are the employee's and HR's."
    return out
