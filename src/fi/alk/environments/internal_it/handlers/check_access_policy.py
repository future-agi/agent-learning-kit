import json

def _source(db):
    today = (db.one("SELECT now FROM clock") or {"now": "2026-10-26T09:00:00Z"})["now"][:10]
    rows = [f for f in db.query("SELECT * FROM drive_files WHERE policy_family = 'access_policy' AND status = 'approved' AND NOT is_deleted") if f["effective_date"] <= today and f["mime_type"] != "application/octet-stream"]
    rows.sort(key=lambda f: f["effective_date"], reverse=True)
    return {"file_id": rows[0]["file_id"], "title": rows[0]["title"], "version": rows[0]["version"]} if rows else None


def handle(args, db):
    emp_id = args.get("employee_id")
    app_id = args.get("app_id")
    role_name = args.get("role_name")
    if not emp_id or not app_id or not role_name:
        raise ToolError("Missing required parameters: employee_id, app_id, role_name")
    emp = db.one("SELECT * FROM employees WHERE employee_id = ?", [emp_id])
    if emp is None:
        raise ToolError(f"Employee {emp_id!r} not found.")
    # The answer says which roles the employee holds, so it is theirs, their manager's or IT's.
    _act_for(db, emp_id)
    app = db.one("SELECT * FROM applications WHERE app_id = ?", [app_id])
    if app is None:
        raise ToolError(f"Application {app_id!r} not found.")
    role = db.one("SELECT * FROM roles WHERE app_id = ? AND role_name = ?", [app_id, role_name])
    if role is None:
        raise ToolError(f"Role {role_name!r} not found for application {app_id!r}.")
    # Every answer names the policy it is given under, refusals included.
    source = _source(db)
    if emp["status"] != "active":
        return {"allowed": False, "status": "inactive_employee", "sod_conflict": False, "approval_required": role["requires_approval"], "approver_ids": [], "reason": f"Employee {emp_id} is not active; inactive employees receive no access.", "source": source}
    if db.one("SELECT 1 FROM entitlements WHERE employee_id = ? AND app_id = ? AND role_name = ? AND status = 'active'", [emp_id, app_id, role_name]):
        return {"allowed": False, "status": "already_assigned", "sod_conflict": False, "approval_required": role["requires_approval"], "approver_ids": [], "reason": f"Employee {emp_id} already has active role '{role_name}' on {app_id}.", "source": source}
    sod_policy = db.one("SELECT value FROM environment_policy WHERE name = 'sod_enabled'")
    if json.loads(sod_policy["value"]) if sod_policy else True:
        active = [r["role_name"] for r in db.query("SELECT role_name FROM entitlements WHERE employee_id = ? AND app_id = ? AND status = 'active'", [emp_id, app_id])]
        for rule in db.query("SELECT * FROM sod_rules WHERE app_id = ?", [app_id]):
            if (rule["role_a"] == role_name and rule["role_b"] in active) or (rule["role_b"] == role_name and rule["role_a"] in active):
                return {"allowed": False, "status": "sod_violation", "sod_conflict": True, "approval_required": role["requires_approval"], "approver_ids": [], "reason": f"Separation of Duties violation: cannot hold both '{rule['role_a']}' and '{rule['role_b']}' ({rule['description']}).", "rule_id": rule["rule_id"], "source": source}
    chain = _approvers(emp, app, role)
    return {
        "allowed": True,
        "status": "permitted",
        "sod_conflict": False,
        "approval_required": role["requires_approval"],
        "approver_ids": chain,
        "reason": "Policy check passed. Required approvals identified, in order." if chain else "Policy check passed. This role needs no approval.",
        "source": source
    }


def _caller(db):
    """The employee signed in to this conversation. The session sets it; nothing the desk is told
    or passes as an argument changes it."""
    row = db.one("SELECT employee_id FROM session_caller WHERE id = 'default'")
    return row["employee_id"] if row else None


def _act_for(db, employee_id, also=()):
    """The signed-in caller, if they may act for or see `employee_id`'s access: the employee
    themselves, their manager, someone in `also` (the request's approvers), or an active member of
    IT operations. Naming an employee is not being them, so anyone else is refused."""
    caller = _caller(db)
    if caller and (caller == employee_id or caller in also):
        return caller
    me = db.one("SELECT department, status FROM employees WHERE employee_id = ?", [caller]) if caller else None
    if me and me["status"] == "active":
        if me["department"] == "it_operations":
            return caller
        emp = db.one("SELECT manager_id FROM employees WHERE employee_id = ?", [employee_id])
        if emp and emp["manager_id"] == caller:
            return caller
    raise ToolError(f"Permission denied: the person signed in to this conversation ({caller or 'nobody'}) may not act for or see the access of {employee_id}.", category="permission_denied")


def _approvers(emp, app, role):
    """Who must approve, in order: the catalogue's manager, application owner, or both (manager
    first, then owner). An employee without a manager goes to the application owner, and nobody
    approves their own request: a step that would be theirs is left to the other approver."""
    if role["requires_approval"] == "none":
        return []
    manager, owner = emp["manager_id"], app["owner_id"]
    chain = {"app_owner": [owner], "both": [manager or owner, owner]}.get(role["requires_approval"], [manager or owner])
    chain = [p for p in chain if p and p != emp["employee_id"]]
    if not chain and manager and manager != emp["employee_id"]:
        chain = [manager]
    return [p for i, p in enumerate(chain) if p not in chain[:i]]
