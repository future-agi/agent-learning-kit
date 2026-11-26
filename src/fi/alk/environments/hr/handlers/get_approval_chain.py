def handle(args, db):
    employee_id = args.get("employee_id")
    working_days = args.get("working_days")
    if not employee_id:
        raise ToolError("employee_id is required.")
    emp = db.one("SELECT * FROM employees WHERE employee_id = ?", (employee_id,))
    if not emp:
        raise ToolError(f"Employee {employee_id} not found.")
    mgr = db.one("SELECT employee_id, name FROM employees WHERE employee_id = ?", (emp["manager_id"],))
    chain = [{"step": "manager", "employee_id": mgr["employee_id"], "name": mgr["name"]}] if mgr else []
    if isinstance(working_days, (int, float)) and not isinstance(working_days, bool) and working_days > 10 and mgr:
        top = db.one("SELECT e.employee_id, e.name FROM employees m JOIN employees e ON e.employee_id = m.manager_id WHERE m.employee_id = ?", (mgr["employee_id"],))
        if top:
            chain.append({"step": "manager's manager", "employee_id": top["employee_id"], "name": top["name"]})
    partner = db.one("SELECT p.employee_id, e.name FROM hr_partners p JOIN employees e ON e.employee_id = p.employee_id WHERE p.region = ?", (emp["region"],))
    return {"employee_id": employee_id, "approvers": chain, "hr_partner": {"employee_id": partner["employee_id"], "name": partner["name"]} if partner else None, "rule": "Leave is approved only in the HR system by the approvers listed; a chat message is not an approval."}
