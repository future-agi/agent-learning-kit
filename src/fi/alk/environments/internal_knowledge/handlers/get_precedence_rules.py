

def _employee(db, employee_id):
    if not employee_id or not isinstance(employee_id, str):
        raise ToolError("employee_id is required: the employee asking.", category="invalid_input")
    e = db.one("SELECT * FROM employees WHERE employee_id = ?", [employee_id])
    if e is None:
        raise ToolError(f"Employee {employee_id!r} not found.", category="not_found")
    if e["status"] != "active":
        raise ToolError(f"Employee {employee_id} is not active; the knowledge desk serves active employees only.", category="permission_denied")
    return e


def handle(args, db):
    e = _employee(db, args.get("employee_id"))
    fams = [f for f in (args.get("family"), args.get("other_family")) if f]
    rules = db.query("SELECT * FROM precedence_rules ORDER BY rule_id")
    if fams:
        rules = [r for r in rules if all(f in (r["prevailing_family"], r["subordinate_family"]) for f in fams)]
    out = {"rules": rules, "configured": bool(rules)}
    if fams and not rules:
        out["note"] = "No precedence is configured between these documents: report the conflict and route it to the document owners."
    return out
