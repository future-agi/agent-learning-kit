import json

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
    emp = db.one("SELECT * FROM employees WHERE employee_id = ?", (employee_id,))
    if not emp:
        raise ToolError(f"Employee {employee_id} not found.")
    # The answer carries the employee's available days, so it is theirs or HR's to see.
    caller = _caller(db)
    if not _sees(caller, employee_id):
        raise ToolError(f"Permission denied: {caller['employee_id']} may not see the leave of {employee_id}.")
    row = db.one("SELECT value FROM environment_policy WHERE name = 'allowed_leave_types'")
    allowed = json.loads(row["value"]) if row else "annual,sick,unpaid,parental,bereavement"
    allowed = [t.strip() for t in allowed.split(",")] if isinstance(allowed, str) else list(allowed)
    today = (db.one("SELECT now FROM clock") or {"now": "2026-10-26T09:00:00Z"})["now"][:10]
    out = []
    for leave_type in allowed:
        topic = f"{leave_type}_leave"
        pol = db.one("SELECT * FROM hr_policies WHERE topic = ? AND region IN (?, 'ALL') AND effective_from <= ? AND effective_to >= ? ORDER BY region DESC", (topic, emp["region"], today, today))
        bal = db.one("SELECT available_days FROM leave_balances WHERE employee_id = ? AND leave_type = ?", (employee_id, leave_type))
        out.append({"leave_type": leave_type, "needs_balance": leave_type not in ("unpaid", "bereavement", "parental"), "available_days": float(bal["available_days"]) if bal else None, "policy_id": pol["policy_id"] if pol else None, "policy_title": pol["title"] if pol else None})
    return {"employee_id": employee_id, "region": emp["region"], "leave_types": out}
