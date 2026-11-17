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
    employee_id = args.get("employee_id")
    if not employee_id:
        raise ToolError("employee_id is required.")
    caller = _caller(db, args.get("actor_id"))
    emp = db.one("SELECT employee_id FROM employees WHERE employee_id = ?", (employee_id,))
    if not emp:
        raise ToolError(f"Employee {employee_id} not found.")
    if not _sees(caller, employee_id):
        raise ToolError(f"Permission denied: {caller['employee_id']} may not see the balances of {employee_id}.")
    leave_type = args.get("leave_type")
    if leave_type:
        rows = db.query("SELECT leave_type, allocated_days, used_days, reserved_days, available_days FROM leave_balances WHERE employee_id = ? AND leave_type = ?", (employee_id, leave_type))
    else:
        rows = db.query("SELECT leave_type, allocated_days, used_days, reserved_days, available_days FROM leave_balances WHERE employee_id = ?", (employee_id,))
    return {
        "employee_id": employee_id,
        "balances": [
            {
                "leave_type": r["leave_type"],
                "allocated_days": float(r["allocated_days"]),
                "used_days": float(r["used_days"]),
                "reserved_days": float(r["reserved_days"]),
                "available_days": float(r["available_days"])
            }
            for r in rows
        ]
    }
